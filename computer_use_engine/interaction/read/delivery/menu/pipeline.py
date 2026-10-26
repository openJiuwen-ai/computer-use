"""Menu numbering, fixed-scope recheck and source-preserving delivery."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.menus.pipeline import (
    MenuCollectionPipeline,
    MenuDirectoryCollection,
)
from computer_use.interaction.read.acquisition.menus.query import NativeMenus
from computer_use.interaction.read.acquisition.scope.pipeline import WorkingScopeServiceNode
from computer_use.interaction.read.app import NativeAppReader, failure
from computer_use.interaction.read.delivery.menu.model import (
    PURE,
    QUERY,
    CollectedMenuObservation,
    MenuDirectoryLease,
    MenuObservationPlan,
    MenuSourceRenderRequest,
    MenuTextDraft,
    RenderedMenuSources,
)
from computer_use.interaction.read.delivery.menu.passes.assemble_menu_observation import (
    stage as assemble_menu_observation,
)
from computer_use.interaction.read.delivery.menu.passes.assign_menu_window_references import (
    stage as assign_menu_window_references,
)
from computer_use.interaction.read.delivery.menu.passes.bind_menu_context import (
    stage as bind_menu_context,
)
from computer_use.interaction.read.delivery.menu.passes.check_menu_delivery_budget import (
    stage as check_menu_delivery_budget,
)
from computer_use.interaction.read.delivery.menu.passes.format_menu_header import (
    stage as format_menu_header,
)
from computer_use.interaction.read.delivery.menu.passes.format_menu_sources import (
    stage as format_menu_sources,
)
from computer_use.interaction.read.delivery.menu.passes.menu_has_working_window import (
    stage as menu_has_working_window,
)
from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
from computer_use.interaction.read.numbering.pipeline import NumberSnapshotsPipeline
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.pipeline import Contract, Pipeline, Scope, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, NoReturn

    from computer_use.interaction.read.delivery.menu.model import MenuObservationRequest
    from computer_use.interaction.read.model import MenuAppObservation
    from computer_use.pipeline.core import Control, Step


class PrepareMenuSources(Node["MenuSourceRenderRequest", "RenderedMenuSources"]):
    """Prepare the snapshots belonging to one menu read.

    Freeze each source projection with its observed context and compactness option.
    Retain the prepared documents in source order for final text rendering.

    Attributes:
        preparation: Pure snapshot preparation flow shared by the observed sources.
    """

    def __init__(self) -> None:
        self.preparation = PrepareSnapshotPipeline()
        self.children = (self.preparation,)
        super().__init__("PrepareMenuSources", Contract(MenuSourceRenderRequest, RenderedMenuSources, PURE))

    def _run(self, request: MenuSourceRenderRequest, control: Control) -> RenderedMenuSources:
        values = []
        for part in request.menu.parts:
            if isinstance(part.tree, SemanticSnapshot):
                prepared = self.preparation.run(SnapshotRenderRequest(part.tree, compact=request.compact), control)
                values.append(DisplayDocument.tree(prepared))
            else:
                values.append(DisplayDocument.join((part.tree.render(compact=request.compact)["text"],)))
        return RenderedMenuSources(request, tuple(values))

    async def _arun(self, request: MenuSourceRenderRequest, control: Control) -> RenderedMenuSources:
        return self._run(request, control)


class MenuSourcesPresentationPipeline(Pipeline["MenuSourceRenderRequest", "DisplayDocument"]):
    """Organize menu source projections and discovery notes into one document.

    Keep source boundaries and their compactness options for final rendering.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[MenuSourceRenderRequest, DisplayDocument]:
        return Sequence(name="MenuSourcesPresentationPipeline", steps=[PrepareMenuSources(), format_menu_sources()])


class CollectMenuObservation(Node["MenuObservationPlan", "CollectedMenuObservation"]):
    """Collect the menu evidence selected by a menu observation plan.

    Resolve the application and native context, then collect the requested path with its
    field and node limits. Pair the resulting menu sources with the observation request and
    directory.

    Attributes:
        services: Application reader providing bounded native menu collection.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        self.children = (MenuCollectionPipeline(services.menus),)
        super().__init__(
            "CollectMenuObservation",
            Contract(MenuObservationPlan, CollectedMenuObservation, self.children[0].contract.effects),
        )

    async def _arun(self, plan: MenuObservationPlan, control: Control) -> CollectedMenuObservation:
        request = plan.request
        collect = self.services.menus.collect
        options: Record = dict(
            context=plan.context,
            source_directory=request.directory,
            path=request.path,
            summary=not request.path,
            **request.limits,
        )
        if getattr(collect, "__func__", None) is NativeMenus.collect:
            options["_control"] = control
        menu = await collect(request.application["id"], cast("Callable[[], int]", control.remaining), **options)
        return CollectedMenuObservation(request, menu, request.directory)

    def _run(self, value: MenuObservationPlan, control: Control) -> CollectedMenuObservation:
        raise TypeError("Menu observation requires arun()")


class NumberMenuSources(Node["CollectedMenuObservation", "CollectedMenuObservation"]):
    """Apply shared aliases to the collected menu snapshots.

    Number every source tree using the application reader's reference allocator. Replace the
    menu parts with their numbered snapshots before rendering.

    Attributes:
        services: Application reader that owns the shared display-reference allocator.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        self.children = (NumberSnapshotsPipeline(),)
        super().__init__(
            "NumberMenuSources",
            Contract(CollectedMenuObservation, CollectedMenuObservation, self.children[0].contract.effects),
        )

    def _run(self, value: CollectedMenuObservation, control: Control) -> CollectedMenuObservation:
        number = self.services.number_snapshots
        sources = (part.tree for part in value.menu.parts)
        if getattr(number, "__func__", None) is NativeAppReader.number_snapshots:
            numbered = number(sources, _control=control)
        else:
            numbered = number(sources)
        menu = replace(
            value.menu, parts=tuple(replace(part, tree=tree) for part, tree in zip(value.menu.parts, numbered))
        )
        return replace(value, menu=menu)

    async def _arun(self, value: CollectedMenuObservation, control: Control) -> CollectedMenuObservation:
        return self._run(value, control)


class RecheckMenuDirectory(Pipeline["CollectedMenuObservation", "CollectedMenuObservation"]):
    """Refresh directory evidence after collecting the menus.

    Rediscover the fixed working scope when present, otherwise read the application's
    current surfaces. Attach the resulting catalogue to the collected menu observation.

    Attributes:
        services: Application reader providing fixed-scope discovery and surface catalogue
            queries.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[CollectedMenuObservation, CollectedMenuObservation]:
        services = self.services
        directory = MenuDirectoryCollection(services.directory)

        async def bound(value: CollectedMenuObservation, control: Control) -> CollectedMenuObservation:
            discover = services._discover
            if getattr(discover, "__func__", None) is NativeAppReader._discover:
                _, current = await discover(
                    value.request.selection, cast("Callable[[], int]", control.remaining), _control=control
                )
            else:
                _, current = await discover(value.request.selection, cast("Callable[[], int]", control.remaining))
            return replace(value, directory=current)

        @asynccontextmanager
        async def unbound_scope(value: CollectedMenuObservation, control: Control) -> AsyncIterator[MenuDirectoryLease]:
            async with services.port.calls.operation(None, cast("Callable[[], int]", control.remaining)) as read:
                if read.target.session != value.menu.target.session:
                    raise failure("stale_target", "The menu belongs to a previous driver session.")
                yield MenuDirectoryLease(value, read)

        class UnboundDirectory(Node):
            """Collect the current surfaces for a menu-only application observation.

            Query the application directory through the active operation and attach it to the
            collected menu evidence.
            """

            def __init__(self) -> None:
                self.children = (directory,)
                super().__init__(
                    "CollectUnboundMenuDirectory",
                    Contract(MenuDirectoryLease, CollectedMenuObservation, QUERY),
                )

            async def _arun(self, value: MenuDirectoryLease, control: Control) -> CollectedMenuObservation:
                current = await directory.arun(
                    (
                        value.read,
                        dict(kind="surfaces", application=value.observed.request.application["id"]),
                    ),
                    control,
                )
                return replace(value.observed, directory=current)

            def _run(self, value: Any, control: Control) -> NoReturn:
                raise TypeError("Menu directories require arun()")

        return Select(
            name="RecheckMenuDirectory",
            selector=menu_has_working_window(),
            branches={
                True: WorkingScopeServiceNode(
                    "RecheckBoundMenuDirectory",
                    services,
                    bound,
                    CollectedMenuObservation,
                    CollectedMenuObservation,
                ),
                False: Scope(
                    name="UnboundMenuDirectoryScope",
                    using=unbound_scope,
                    body=NativeScopeBody(services.port, UnboundDirectory(), lambda value: value.read.remaining),
                ),
            },
        )


class AppendMenuDocuments(Node["MenuTextDraft", "MenuTextDraft"]):
    """Append source presentations to the menu observation draft.

    Prepare menu-source documents and append them after the structured header.
    Return the draft for budget checking and final assembly.

    Attributes:
        sources: Menu-source preparation flow over the collected snapshots.
    """

    def __init__(self) -> None:
        self.sources = MenuSourcesPresentationPipeline()
        self.children = (self.sources,)
        super().__init__("AppendMenuDocuments", Contract(MenuTextDraft, MenuTextDraft, PURE))

    def _run(self, value: MenuTextDraft, control: Control) -> MenuTextDraft:
        document = self.sources.run(MenuSourceRenderRequest(value.observed.menu, compact=True), control)
        return replace(value, lines=(*value.lines, document))

    async def _arun(self, value: MenuTextDraft, control: Control) -> MenuTextDraft:
        return self._run(value, control)


class MenuObservationPipeline(Pipeline["MenuObservationRequest", "MenuAppObservation"]):
    """Collect and present an application's selected menu context.

    Resolve the native context, collect and number its menu snapshots, and recheck the
    window catalogue. Format the scope header and menu sources into a result retaining the
    native menu evidence.

    Attributes:
        services: Application reader providing menu collection, shared aliases, and
            directory rechecks.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[MenuObservationRequest, MenuAppObservation]:
        services = self.services

        return Sequence(
            name="MenuObservationPipeline",
            steps=[
                bind_menu_context(services=services),
                CollectMenuObservation(services),
                NumberMenuSources(services),
                RecheckMenuDirectory(services),
                assign_menu_window_references(services=services),
                format_menu_header(),
                AppendMenuDocuments(),
                check_menu_delivery_budget(),
                assemble_menu_observation(),
            ],
        )


if TYPE_CHECKING:
    from typing import Callable
if TYPE_CHECKING:
    from computer_use.protocol.records import Record
