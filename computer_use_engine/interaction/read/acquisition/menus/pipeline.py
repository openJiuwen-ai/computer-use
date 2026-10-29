# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only menu source selection, bounded path matching and fact collection.

Window sources and application roots use separate native scopes. A path only
selects observed nodes; no menu is opened and no command or focus is sent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryCollectionPipeline
from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
from computer_use.interaction.read.acquisition.facts.pipeline import FactCollectionPipeline
from computer_use.interaction.read.acquisition.menus.model import (
    QUERY,
    CollectedMenuParts,
    CollectedMenuRoots,
    DiscoveredWindowMenus,
    MenuFactPart,
    MenuLease,
    MenuLevel,
    MenuPartsPlan,
    MenuPathRequest,
    SelectedWindowMenus,
)
from computer_use.interaction.read.acquisition.menus.passes.assemble_menu_sources import (
    stage as assemble_menu_sources,
)
from computer_use.interaction.read.acquisition.menus.passes.check_menu_collection_budget import (
    stage as check_menu_collection_budget,
)
from computer_use.interaction.read.acquisition.menus.passes.collect_menu_level import (
    stage as collect_menu_level,
)
from computer_use.interaction.read.acquisition.menus.passes.collect_selected_menu import (
    stage as collect_selected_menu,
)
from computer_use.interaction.read.acquisition.menus.passes.discover_window_menu_sources import (
    stage as discover_window_menu_sources,
)
from computer_use.interaction.read.acquisition.menus.passes.match_menu_title import (
    stage as match_menu_title,
)
from computer_use.interaction.read.acquisition.menus.passes.normalize_menu_depth import (
    stage as normalize_menu_depth,
)
from computer_use.interaction.read.acquisition.menus.passes.plan_window_menu_parts import (
    stage as plan_window_menu_parts,
)
from computer_use.interaction.read.acquisition.menus.passes.select_application_menu_root import (
    stage as select_application_menu_root,
)
from computer_use.interaction.read.acquisition.menus.passes.select_menu_source_kind import (
    stage as select_menu_source_kind,
)
from computer_use.interaction.read.acquisition.menus.passes.select_submenu import (
    stage as select_submenu,
)
from computer_use.interaction.read.acquisition.menus.passes.validate_window_menu_context import (
    stage as validate_window_menu_context,
)
from computer_use.interaction.read.acquisition.menus.query import (
    MenuObservation,
    MenuQuery,
    menu_failure,
)
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.native.contracts import Binding
from computer_use.pipeline import Contract, Pipeline, Scope, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, NoReturn

    from computer_use.interaction.read.acquisition.menus.model import ApplicationMenuTarget, MenuCollectionRequest
    from computer_use.interaction.read.acquisition.menus.query import MenuRead, NativeMenus
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


class MenuPathPipeline(Node["MenuPathRequest", "Binding"]):
    """Resolve an ordered label path within a native menu scope.

    Read and match each menu level, then resolve the submenu container for the next
    component. Return a binding for the final resolved menu target.

    Attributes:
        level: Per-level collection, exact-name matching, and submenu-selection flow.
    """

    def __init__(self) -> None:
        self.level = Sequence(
            name="MenuPathLevel",
            steps=[collect_menu_level(), match_menu_title(), collect_selected_menu(), select_submenu()],
        )
        self.children = (self.level,)
        super().__init__("MenuPathPipeline", Contract(MenuPathRequest, Binding, self.level.contract.effects))

    async def _arun(self, request: MenuPathRequest, control: Control) -> Binding:
        current, ancestors = request.query.root.node, (request.query.root.node,)
        for index, title in enumerate(request.path):
            current, ancestors = await self.level.arun(MenuLevel(request, current, ancestors, index, title), control)
        return replace(request.query.root, node=current)

    def _run(self, request: MenuPathRequest, control: Control) -> Binding:
        raise TypeError("Native menu matching requires arun()")


class MenuDirectoryCollection(Node["tuple[OperationLease[Binding], Record]", "DirectoryFacts"]):
    """Collect a menu directory query under the active native operation.

    Pass the lease and query to the directory service and return its catalogue. Expose the
    shared directory-collection flow as this adapter's child.

    Attributes:
        directory: Native directory service used for application menu-root discovery.
    """

    def __init__(self, directory: NativeDirectory) -> None:
        self.directory = directory
        self.children = (DirectoryCollectionPipeline(directory.port),)
        super().__init__("MenuDirectoryCollection", Contract(tuple, dict, self.children[0].contract.effects))

    async def _arun(self, value: tuple[OperationLease[Binding], Record], control: Control) -> DirectoryFacts:
        read, query = value
        collect = self.directory.collect
        if getattr(collect, "__func__", None) is NativeDirectory.collect:
            return await collect(read, query, _control=control)
        return await collect(read, query)

    def _run(self, value: tuple[OperationLease[Binding], Record], control: Control) -> DirectoryFacts:
        raise TypeError("Native menu directories require arun()")


class MenuPartCollection(Node["MenuPartsPlan", "CollectedMenuParts"]):
    """Collect a native fact snapshot for each planned menu source.

    Apply the source's node budget and scope label within the existing operation. Return the
    ordered menu snapshots paired with their collection plan.

    Attributes:
        reader: Native fact reader used to collect and normalize each menu source.
    """

    def __init__(self, reader: NativeReader) -> None:
        self.reader = reader
        self.children = (FactCollectionPipeline(reader),)
        super().__init__(
            "CollectMenuFactParts",
            Contract(MenuPartsPlan, CollectedMenuParts, self.children[0].contract.effects),
        )

    async def _arun(self, plan: MenuPartsPlan, control: Control) -> CollectedMenuParts:
        request, parts = plan.request, []
        for part in plan.parts:
            options: Record = dict(
                node=part.target.node,
                fields=request.fields,
                max_nodes=part.max_nodes,
                max_depth=1 if request.summary else request.max_depth,
                scope_kind=part.scope_kind,
            )
            collect = self.reader.collect
            if getattr(collect, "__func__", None) is NativeReader.collect:
                options["_control"] = control
            tree = await collect(plan.read, **options)
            parts.append(MenuObservation(part.target, tree, part.directory, tuple(request.path)))
        return CollectedMenuParts(plan, tuple(parts))

    def _run(self, value: MenuPartsPlan, control: Control) -> CollectedMenuParts:
        raise TypeError("Native menu facts require arun()")


class WindowMenuSelection(Node["DiscoveredWindowMenus", "SelectedWindowMenus"]):
    """Resolve the requested path within discovered window-menu sources.

    Keep each discovered root when the path is empty, otherwise resolve the path under each
    source. Return the selected native menu bindings alongside the discovery evidence.

    Attributes:
        port: Native query port used to resolve menu paths within the active operation.
        path: Ordered menu-label resolution flow reused for each discovered root.
    """

    def __init__(self, port: NativePort) -> None:
        self.port, self.path = port, MenuPathPipeline()
        self.children = (self.path,)
        super().__init__("SelectWindowMenuPaths", Contract(DiscoveredWindowMenus, SelectedWindowMenus, QUERY))

    async def _arun(self, value: DiscoveredWindowMenus, control: Control) -> SelectedWindowMenus:
        request, read, sources = value.lease.request, value.lease.read, value.sources
        # One query cache belongs to this bounded matching phase, separately
        # from the earlier window discovery cache and each later fact read.
        query = MenuQuery(
            self.port,
            read.token,
            request.context,
            read.remaining,
            max_nodes=request.max_nodes,
            max_depth=request.max_depth,
        )
        selected, missing = [], []
        for target in sources.targets:
            query.root = target
            try:
                selected.append(await query.resolve_menu(request.path, _control=control) if request.path else target)
            except ComputerUseError as error:
                if error.code != "element_not_found" or error.effect != "none":
                    raise
                missing.append(error)
        if not selected:
            raise menu_failure(
                "observation_incomplete" if sources.gaps else "element_not_found",
                f"No match in {len(sources.targets)} observed menu sources. "
                + ("Other sources may remain unread. " if sources.gaps else "")
                + missing[0].detail,
            )
        return SelectedWindowMenus(value, tuple(selected))

    def _run(self, value: DiscoveredWindowMenus, control: Control) -> NoReturn:
        raise TypeError("Native menu matching requires arun()")


class ApplicationMenuSelection(Node["tuple[ApplicationMenuTarget, OperationLease[Binding]]", "MenuPartsPlan"]):
    """Resolve the requested path beneath an application menu root.

    Use the observed menu-bar binding directly for an empty path or traverse the requested
    labels. Build the menu-part plan using the resolved target and request limits.

    Attributes:
        port: Native query port used for the application menu path.
        path: Ordered menu-label resolution flow used when a path is requested.
    """

    def __init__(self, port: NativePort) -> None:
        self.port, self.path = port, MenuPathPipeline()
        self.children = (self.path,)
        super().__init__("SelectApplicationMenuPath", Contract(tuple, MenuPartsPlan, QUERY))

    async def _arun(self, value: tuple[ApplicationMenuTarget, OperationLease], control: Control) -> MenuPartsPlan:
        selected, read = value
        request, target = selected.request, selected.target
        query = (
            MenuQuery(
                self.port, read.token, target, read.remaining, max_nodes=request.max_nodes, max_depth=request.max_depth
            )
            if request.path
            else None
        )
        target = await query.resolve_menu(request.path, _control=control) if request.path else target
        return MenuPartsPlan(
            request, read, (MenuFactPart(target, request.max_nodes, selected.roots, "applicationMenu"),), target
        )

    def _run(self, value: ApplicationMenuTarget, control: Control) -> CollectedMenuParts:
        raise TypeError("Native menu matching requires arun()")


class MenuCollectionPipeline(Pipeline["MenuCollectionRequest", "MenuRead"]):
    """Collect native snapshots for an application or window menu read.

    Select application-root or window-menu discovery according to driver capabilities, then
    resolve the optional path. Collect the planned source snapshots and return their common
    scope and discovery gaps.

    Attributes:
        services: Native menu services providing the directory, fact reader, and query port.
    """

    def __init__(self, services: NativeMenus) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[MenuCollectionRequest, MenuRead]:
        services = self.services
        port = services.port
        directory = MenuDirectoryCollection(services.directory)
        parts = MenuPartCollection(services.reader)

        @asynccontextmanager
        async def window_scope(request: MenuCollectionRequest, control: Control) -> AsyncIterator[MenuLease]:
            async with port.calls.operation(
                replace(request.context, node=None), cast("Callable[[], int]", control.remaining)
            ) as read:
                yield MenuLease(request, read)

        @asynccontextmanager
        async def roots_scope(request: MenuCollectionRequest, control: Control) -> AsyncIterator[MenuLease]:
            """Roots scope.

            Args:
                request: Parsed request carrying the original operation scope and options.

            Yields:
                The scoped stage value; the existing owner remains responsible for cleanup.
            """
            async with port.calls.operation(None, cast("Callable[[], int]", control.remaining)) as read:
                if request.context is not None and read.target.session != request.context.session:
                    raise menu_failure("stale_target", "The menu context belongs to a previous driver session.")
                yield MenuLease(request, read)

        @asynccontextmanager
        async def application_scope(
            target: ApplicationMenuTarget, control: Control
        ) -> AsyncIterator[tuple[ApplicationMenuTarget, OperationLease]]:
            async with port.calls.operation(
                replace(target.target, node=None), cast("Callable[[], int]", control.remaining)
            ) as read:
                yield target, read

        class RootsCollection(Node):
            """Read application accessibility roots for menu discovery.

            Query the root catalogue using the selected application and return it with the menu
            collection request.
            """

            def __init__(self) -> None:
                self.children = (directory,)
                super().__init__("CollectApplicationMenuRoots", Contract(MenuLease, CollectedMenuRoots, QUERY))

            async def _arun(self, lease: MenuLease, control: Control) -> CollectedMenuRoots:
                roots = await directory.arun(
                    (lease.read, dict(kind="roots", application=lease.request.application)), control
                )
                return CollectedMenuRoots(lease.request, roots)

            def _run(self, value: Any, control: Control) -> NoReturn:
                raise TypeError("Native menu roots require arun()")

        window = Sequence(
            name="WindowMenuCollectionPipeline",
            steps=[
                validate_window_menu_context(),
                Scope(
                    name="WorkingWindowMenuScope",
                    using=window_scope,
                    body=NativeScopeBody(
                        port,
                        Sequence(
                            name="DiscoverAndCollectWindowMenus",
                            steps=[
                                discover_window_menu_sources(port=port),
                                WindowMenuSelection(port),
                                plan_window_menu_parts(),
                                parts,
                            ],
                        ),
                        lambda value: value.read.remaining,
                    ),
                ),
            ],
        )
        application = Sequence(
            name="ApplicationMenuCollectionPipeline",
            steps=[
                Scope(
                    name="ApplicationMenuRootsScope",
                    using=roots_scope,
                    body=NativeScopeBody(port, RootsCollection(), lambda value: value.read.remaining),
                ),
                select_application_menu_root(),
                Scope(
                    name="ApplicationMenuFactScope",
                    using=application_scope,
                    body=NativeScopeBody(
                        port,
                        Sequence(
                            name="SelectAndCollectApplicationMenu",
                            steps=[ApplicationMenuSelection(port), parts],
                        ),
                        lambda value: value[1].remaining,
                    ),
                ),
            ],
        )
        return Sequence(
            name="MenuCollectionPipeline",
            steps=[
                normalize_menu_depth(),
                Select(
                    name="MenuSourceKind",
                    selector=select_menu_source_kind(port=port),
                    branches={False: window, True: application},
                ),
                check_menu_collection_budget(),
                assemble_menu_sources(),
            ],
        )


if TYPE_CHECKING:
    from computer_use.protocol.records import Record
if TYPE_CHECKING:
    from typing import Callable
