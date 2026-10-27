"""A fixed-window read is a bounded acquisition loop, never an input retry."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.choices.pipeline import WindowChoiceServiceNode
from computer_use.interaction.read.acquisition.components.model import ComponentRequest
from computer_use.interaction.read.acquisition.components.pipeline import ComponentPipeline
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.directory.service import select_surface
from computer_use.interaction.read.acquisition.helpers import window_binding
from computer_use.interaction.read.acquisition.loading import initialization_requested
from computer_use.interaction.read.acquisition.model import (
    CollectedAttempt,
    Components,
    ReadPlan,
    ReadRunState,
    RecheckedAttempt,
    Refresh,
)
from computer_use.interaction.read.acquisition.passes.classify_read_attempt import (
    stage as classify_read_attempt,
)
from computer_use.interaction.read.acquisition.passes.collect_window_overview import (
    stage as collect_window_overview,
)
from computer_use.interaction.read.acquisition.passes.keep_components import (
    stage as keep_components,
)
from computer_use.interaction.read.acquisition.passes.read_refresh_gate import (
    stage as read_refresh_gate,
)
from computer_use.interaction.read.acquisition.passes.retain_new_scope_for_recollection import (
    stage as retain_new_scope_for_recollection,
)
from computer_use.interaction.read.acquisition.passes.select_collection_kind import (
    stage as select_collection_kind,
)
from computer_use.interaction.read.acquisition.passes.select_refresh_wait import (
    stage as select_refresh_wait,
)
from computer_use.interaction.read.acquisition.passes.wait_capture_geometry import (
    stage as wait_capture_geometry,
)
from computer_use.interaction.read.acquisition.passes.wait_content_change import (
    stage as wait_content_change,
)
from computer_use.interaction.read.acquisition.scope.pipeline import WorkingScopeServiceNode
from computer_use.pipeline import Contract, Pipeline, Repeat, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.model import AcquiredObservation
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control, Step


class _CollectComponents(Node["ReadRunState", "CollectedAttempt"]):
    """Collect components for one acquisition attempt.

    A full attempt records tree and image results against its source directory. A tree
    refresh replaces successful fact collection while retaining earlier images and supported
    evidence when refresh fails.

    Attributes:
        component_pipeline: Window component collection flow used by both attempt forms.
        refresh: Whether this attempt refreshes the tree while retaining earlier components.
    """

    def __init__(self, services: NativeAppReader, *, refresh: bool = False) -> None:
        self.component_pipeline = ComponentPipeline(services)
        self.children = (self.component_pipeline,)
        self.refresh = refresh
        super().__init__("RefreshComponents" if refresh else "CollectComponents", self.component_pipeline.contract)

    async def _arun(self, state: ReadRunState, control: Control) -> CollectedAttempt:
        parts = await self.component_pipeline.arun(
            ComponentRequest(
                state.target,
                dict(state.plan.limits),
                include_image=not self.refresh and state.plan.limits.get("view", "both") != "tree",
                include_tree=state.plan.limits.get("view", "both") != "image",
            ),
            control,
        )
        if not self.refresh:
            return CollectedAttempt(state, Components(state.directory, parts.tree, parts.image, gaps=parts.gaps))
        components, gaps = state.components, parts.gaps
        retained = components.gaps
        if parts.tree is not None:
            retained = tuple(gap for gap in retained if gap.component not in ("tree", "view"))
        elif components.tree is not None:
            gaps += (
                ObservationGap(
                    "tree",
                    "observation_incomplete",
                    "The content refresh failed; the earlier tree and its capture interval are retained.",
                    "read again if newer content is needed",
                ),
            )
        return CollectedAttempt(
            state,
            replace(
                components,
                tree=parts.tree if parts.tree is not None else components.tree,
                gaps=retained + gaps,
                image_precedes_tree=parts.tree is not None,
            ),
        )

    def _run(self, value: ReadRunState, control: Control) -> CollectedAttempt:
        raise TypeError("Component acquisition requires arun()")


class _AppMenuCollection(Node["CollectedAttempt", "CollectedAttempt"]):
    """Attach application-menu facts to a window acquisition.

    When tree observation and application-root support are available, collect a menu summary
    in the selected root context. Attach the menu result or collection gap to the attempt's
    components.

    Attributes:
        services: Application reader providing menu collection and component-failure
            conversion.
    """

    def __init__(self, services: NativeAppReader) -> None:
        from computer_use.interaction.read.acquisition.menus.pipeline import MenuCollectionPipeline

        self.services = services
        self.children = (MenuCollectionPipeline(services.menus),)
        super().__init__(
            "CollectAppMenuFacts",
            Contract(CollectedAttempt, CollectedAttempt, self.children[0].contract.effects),
        )

    async def _arun(self, value: CollectedAttempt, control: Control) -> CollectedAttempt:
        from computer_use.interaction.read.acquisition.menus.query import NativeMenus

        services = self.services
        state, components = value.state, value.components
        if (
            state.plan.limits.get("view") == "image"
            or "nativeApplicationRoots" not in services.port.connection.capabilities
        ):
            return value
        collect = services.menus.collect
        options: Record = dict(context=state.plan.selection.root, summary=True)
        if getattr(collect, "__func__", None) is NativeMenus.collect:
            options["_control"] = control
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        menus, gap = await services._component(
            "menus", lambda: collect(state.plan.selection.application, control.remaining, **options)
        )
        return replace(
            value, components=replace(components, menus=menus, gaps=components.gaps + ((gap,) if gap else ()))
        )

    def _run(self, value: CollectedAttempt, control: Control) -> CollectedAttempt:
        raise TypeError("App menus require arun()")


class FullCollectionPipeline(Pipeline["ReadRunState", "CollectedAttempt"]):
    """Collect the requested components for a new window observation.

    Collect window facts and pixels according to the view, then add supported application
    menus and requested window previews. Return the components paired with their acquisition
    attempt.

    Attributes:
        services: Application reader providing native components, menu sources, and window
            previews.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ReadRunState, CollectedAttempt]:
        services = self.services

        return Sequence(
            name="FullCollectionPipeline",
            steps=[
                _CollectComponents(services),
                _AppMenuCollection(services),
                collect_window_overview(services=services),
            ],
        )


class TreeRefreshPipeline(Pipeline["ReadRunState", "CollectedAttempt"]):
    """Refresh the fact tree within an existing acquisition.

    Run component collection in tree-refresh mode and retain the previous image, menus, and
    overview. Return the refreshed attempt with updated gap and capture-order evidence.

    Attributes:
        services: Application reader used to recollect the selected native fact scope.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ReadRunState, CollectedAttempt]:
        services = self.services
        return Sequence(name="TreeRefreshPipeline", steps=[_CollectComponents(services, refresh=True)])


class AcquisitionPipeline(Pipeline["ReadPlan", "AcquiredObservation"]):
    """Collect a working window and reconcile it with the current scope.

    Resolve the observed surface, collect its components, and recheck the working-window
    directory. Use the refresh gate to complete the observation or consume a bounded scope,
    image, or content refresh.

    Attributes:
        services: Application reader providing scope discovery, component collection, and
            refresh evidence.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ReadPlan, AcquiredObservation]:
        services = self.services

        async def discover(plan: ReadPlan, control: Control) -> ReadRunState:
            """Resolve the selected surface and initialize the acquisition attempt.

            Args:
                plan: Fixed working scope and limits used by bounded component acquisition.
            """
            try:
                target = select_surface(plan.directory, plan.selection.root.root_window)
            except ComputerUseError as error:
                if error.code not in ("ambiguous_target", "observation_incomplete"):
                    raise
                # pylint: disable-next=protected-access
                raise await services._window_choices(
                    error,
                    plan.selection.application,
                    plan.selection.name,
                    plan.directory,
                    cast("Callable[[], int]", control.remaining),
                    selection=plan.selection,
                    _control=control,
                    include_overview=plan.limits.get("overview", True),
                ) from error
            gaps = (
                tuple(
                    ObservationGap(
                        "applications",
                        "observation_incomplete",
                        reason,
                        "use the exact observed application identity",
                    )
                    for reason in plan.applications["stopReasons"]
                )
                if plan.applications is not None
                else ()
            )
            return ReadRunState(
                plan,
                target,
                plan.directory,
                initialization_requested(plan.directory, plan.launch),
                gaps=gaps,
            )

        async def recheck(value: CollectedAttempt, control: Control) -> RecheckedAttempt:
            """Rediscover the working scope after collecting its tree and image components.

            Args:
                value: Read-attempt inputs paired with the components actually captured.
            """
            state, components = value.state, value.components
            # pylint: disable-next=protected-access
            _, directory = await services._discover(
                state.plan.selection,
                cast("Callable[[], int]", control.remaining),
                accessibility=state.plan.limits.get("view") != "image"
                and "nativeReadIsolation" not in services.port.connection.capabilities,
                _control=control,
            )
            # pylint: disable-next=protected-access
            confirmed, gaps = services._complete_scope(directory), state.gaps
            try:
                current = select_surface(directory, state.plan.selection.root.root_window)
            except ComputerUseError as error:
                if error.code not in ("ambiguous_target", "observation_incomplete"):
                    raise
                confirmed, current = False, None
                # pylint: disable-next=protected-access
                gaps += (services._gap("scope", error),)
            target = state.target
            if components.tree is None and current is not None and window_binding(current) == window_binding(target):
                target = current
            return RecheckedAttempt(value, directory, target, current, confirmed, gaps)

        collect = Select(
            name="CollectAttempt",
            selector=select_collection_kind(),
            branches={False: FullCollectionPipeline(services), True: TreeRefreshPipeline(services)},
        )
        wait = Select(
            name="RefreshWaitPipeline",
            selector=select_refresh_wait(),
            branches={
                Refresh.KEEP: keep_components(),
                Refresh.SCOPE: retain_new_scope_for_recollection(),
                Refresh.IMAGE: wait_capture_geometry(),
                Refresh.TREE: wait_content_change(services=services),
            },
        )
        body = Sequence(
            name="AcquireReadAttempt",
            steps=[
                collect,
                WorkingScopeServiceNode("RecheckReadScope", services, recheck, CollectedAttempt, RecheckedAttempt),
                classify_read_attempt(),
                wait,
            ],
        )
        return Sequence(
            name="AcquisitionPipeline",
            steps=[
                WindowChoiceServiceNode("SelectObservedScope", services, discover, ReadPlan, ReadRunState),
                Repeat(name="ReadRefreshLoop", body=body, gate=read_refresh_gate(), max_iterations=2),
            ],
        )


if TYPE_CHECKING:
    from typing import Callable
if TYPE_CHECKING:
    from computer_use.protocol.records import Record
