"""Own one native session and connect public handles to its execution pipeline."""

from __future__ import annotations

import hashlib
import os
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING, cast, overload

from computer_use.diagnostics import localFailure
from computer_use.interaction.pipeline import SessionPipeline
from computer_use.interaction.read.acquisition.images.desktop.service import NativeDesktopReader
from computer_use.interaction.read.acquisition.reader import capturedChildren, capturedField
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.pages import ContentPages
from computer_use.interaction.requests import (
    ExecuteAction,
    FindElement,
    ListApplications,
    ReadApp,
    ReadDesktop,
    ReadElement,
    ReadText,
)
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.scopes import RunningApplication, window_binding
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.captures import captureMetadata
from computer_use.protocol.observation import NativeNode
from computer_use.protocol.primitives import finite, frozen
from computer_use.protocol.session import desktopMode, nativeProtocolVersion
from computer_use.session.bindings import SessionBindings
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import observationCompleted, observationsInvalidated
from computer_use.session.observation_data import retainObservationData

if TYPE_CHECKING:
    from typing import Mapping, NoReturn, Sequence

    from computer_use.interaction.model import ControllerAdapters
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.acquisition.images.overview import WindowOverview
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.model import WindowSelectionRequired
    from computer_use.interaction.records import ElementPath as LookupPath, ElementSnapshot
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.native.contracts import OperationTarget
    from computer_use.native.port import NativePort
    from computer_use.protocol.png import PNG
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts, Record
    from computer_use.session.state import AppState
    from computer_use.types import ActionResult, Observation, TextPage


class SessionController:
    """Own native I/O, handle bindings, and execution services for a Computer.

    All requests enter SessionPipeline. The controller supplies its child flows
    with observation readers, input execution, retained content, and callbacks
    that construct public SDK results.
    """

    def __init__(self, port: NativePort | None = None, *, adapters: ControllerAdapters) -> None:
        """Create the session services and their shared execution entry.

        Args:
            port: Native I/O port to use, or None to create the configured connection.
            adapters: Public-result constructors and image-delivery hooks supplied
                by the SDK host.
        """
        self.adapters = adapters
        self._observation_state = adapters.observation_state
        self._action_result, self._captured_text = adapters.action_result, adapters.captured_text
        self.bindings = SessionBindings()
        self.desktop = DesktopStore(
            DesktopImageAccess(adapters.register_image, adapters.image_delivery_epoch, adapters.image_delivery_required)
        )
        self.transport = (
            port.connection if port is not None else Connection(self.invalidate, protocol_version=nativeProtocolVersion)
        )
        self.port = port or ConnectionPort(
            self.transport, HostPolicy(desktopMode(), os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1")
        )
        self.reader, self.desktop_reader = NativeAppReader(self.port), NativeDesktopReader(self.port)
        self.engine, self.pages = Engine(self.port), ContentPages()
        self.pipeline = SessionPipeline(self)
        from computer_use.interaction.agent.pipeline import AgentInterfacePipeline

        self.interfaces = AgentInterfacePipeline(self)

    @overload
    async def run(
        self, request: ReadApp | ReadDesktop | ObserveUI | ObserveDesktop | ObserveElement
    ) -> Observation: ...

    @overload
    async def run(self, request: ReadElement) -> tuple[Observation, ElementSnapshot]: ...

    @overload
    async def run(self, request: ReadText | ObserveText) -> TextPage: ...

    @overload
    async def run(self, request: FindElement | ResolveElement) -> ElementSnapshot: ...

    @overload
    async def run(self, request: ListApplications) -> tuple[NativeApplicationSummary, ...]: ...

    @overload
    async def run(self, request: DiscoverApps) -> list[AppInfo]: ...

    @overload
    async def run(self, request: ExecuteAction | ActOnElement | UIKeyboard | UIPointer) -> ActionResult: ...

    async def run(self, request: InteractionRequest) -> InteractionResult:
        return await self.pipeline.arun(request)

    def publishObservation(self, event: Record) -> None:
        observationCompleted(dict(source=self, **event))

    def register(self, owner: object, scope: Record) -> None:
        self.bindings.register(owner, scope)

    def state(self, owner: object) -> AppState:
        return self.bindings.state(owner)

    def scope(self, owner: object) -> Record:
        return self.bindings.scope(owner)

    def invalidate(self) -> None:
        self.desktop.reset()
        self.bindings.invalidate()
        observationsInvalidated(self)
        # Handles retain their prior session identity so subsequent validation
        # detects expiration.

    def validateElement(self, target: Binding) -> None:
        self.bindings.validateElement(target, self.transport.session)

    validateSnapshot = validateElement

    def _catalogue(self, state: AppState, directory: DirectoryFacts, windows: Mapping[str, str]) -> None:
        self.bindings.catalogue(state, directory, windows)

    async def call(
        self,
        method: str,
        params: Record,
        owner: object | None = None,
        options: Record | None = None,
    ) -> tuple[NativeApplicationSummary, ...]:
        if method != "listApps" or params:
            self.unavailable(method)
        return await self.run(ListApplications())

    @staticmethod
    def unavailable(method: str) -> NoReturn:
        raise localFailure(
            method + " is not connected to protocol 6 yet",
            "use the supported App/Element routes in this session; no legacy fallback was attempted",
            "mode_unsupported",
        )

    async def readDesktop(self, full: bool = False) -> Observation:
        return await self.run(ReadDesktop(full))

    async def read(
        self,
        owner: object,
        full: bool = False,
        menuPath: Sequence[str] | None = None,
        subtree: None = None,
    ) -> Observation:
        if subtree is not None:
            raise ValueError("Use readElement for an already-bound native element")
        return await self.run(ReadApp(owner, full, menuPath))

    def _choices(self, owner: object, state: AppState, choices: WindowSelectionRequired, full: bool) -> Observation:
        """Publish window choices when the current App view is unresolved.

        Args:
            owner: Handle receiving the directory observation.
            state: Handle state updated with the running application and window aliases.
            choices: Discovery result containing candidate windows and optional startup
                or overview evidence.
            full: Requested read mode; window choices are always published in full.

        Returns:
            Observation listing selectable windows and discovery limitations.
        """
        state.running = RunningApplication(choices.application, choices.display_name, choices.directory["session"])
        if choices.selection is not None:
            state.selection = choices.selection
        self._catalogue(state, choices.directory, self.reader.window_aliases(choices.directory))
        state.observation, state.query_target, state.delivered = None, None, None
        from computer_use.interaction.read.app import window_summary_content
        from computer_use.interaction.read.presentation.content import json_value, template
        from computer_use.interaction.read.presentation.document import DisplayDocument
        from computer_use.session.observation import lazyObservation

        lines = [
            template("App: {}.", json_value(state.name)),
            "Select a window with app.window(ref).",
            (
                "Tree: unavailable; the fixed working window has no unique current view."
                if state.selection
                else "Tree: unavailable; a working window has not been selected."
            ),
            template(
                "Windows: {}",
                window_summary_content(cast("dict", state.directory), state.windows, selection=state.selection),
            ),
        ]
        lines.append(choices.detail)
        lines.extend(
            template("Directory partial: {}", reason) for reason in cast("dict", state.directory)["stopReasons"]
        )
        if choices.launch is not None:
            lines.append(template("Launch: {}", choices.launch["detail"]))
        if choices.overview is not None:
            lines.append(choices.overview.document)
        document = DisplayDocument.join(lines)
        result = lazyObservation(
            document, None, details_document=document, overview=self._overview_image(choices.overview)
        )
        retainObservationData(
            result, [], [], state.windows, state.selection.root.root_window if state.selection else None
        )
        observationCompleted(
            dict(
                source=self,
                owner=owner,
                scope="catalogue:" + state.application,
                view="catalogue",
                state=result,
                refs=frozen({}),
                full=True,
                document=document,
                observed=cast("dict", state.directory)["observed"],
            )
        )
        return result

    def _remember(self, state: AppState, target: Binding, tree: SemanticSnapshot) -> None:
        if {"nativeRelationPages", "nativeNodePages"} & set(self.transport.capabilities):
            self.pages.remember(target, tree)
        self.bindings.remember(state, self._snapshots(target, tree))

    @staticmethod
    def _snapshots(target: Binding, tree: SemanticSnapshot) -> dict[str, ElementSnapshot]:
        """Retain bindings and continuation sources for the tree's element references.

        Args:
            target: Native scope used as the identity prefix for each element binding.
            tree: Semantic snapshot containing raw nodes, fields, and reference mapping.

        Returns:
            Snapshots keyed by display reference, with container classification and
            captured text/child relations for subsequent operations.
        """
        native_nodes = {node["node"]: node for node in tree.facts["nodes"]}
        child_relations = {r["source"]: r for r in tree.facts["relations"] if r["kind"] == "children"}

        def ordinary(token: str, node: NativeNode | None) -> bool:
            """Use native input-scope evidence before the semantic-only fallback."""
            if tree.collection is not None and token in tree.collection.input_containers:
                return tree.collection.input_containers[token]
            if node is None:
                return False
            writable = node.states.editable is True or any(
                action.available is True
                and action.kind in ("set_value", "replace_selection")
                and action.target
                in (("control_value", "value"), ("text", "content"), ("text", "selected_text"), ("text", "selections"))
                for action in node.actions or ()
            )
            return node.role in {"group", "unknown", "window", "sheet", "dialog", "alert"} and not writable

        return cast(
            "dict[str, ElementSnapshot]",
            {
                ref: dict(
                    container=ordinary(node, tree.native.nodes.get(node)),
                    binding=replace(target, node=node, capture=None),
                    scope_kind=tree.scope_kind,
                    source=dict(ref=ref, observed=tree.facts["observed"]),
                    text=capturedField(tree, ref, tree.value_fields.get(ref), nodes=native_nodes),
                    children=capturedChildren(tree, ref, relations=child_relations),
                )
                for ref, node in tree.refs.items()
            },
        )

    def elementSnapshot(self, owner: object, ref: str) -> ElementSnapshot:
        return self.bindings.elementSnapshot(owner, ref, self.transport.session)

    def _overview_image(self, overview: WindowOverview | None) -> PNG | None:
        """Register the overview contact sheet as observation-only image context."""
        if overview is None or overview.image is None:
            return None
        # Overview registration supplies visual context; coordinate dispatch
        # uses the working window's separately delivered full-size capture.
        self.adapters.register_image(overview.image, dict(kind="overview", context=overview.document))
        return overview.image

    @staticmethod
    def _window_ref(state: AppState, target: Binding) -> str | None:
        native = next(
            (
                item["window"]
                for item in (state.directory or {}).get("surfaces", [])
                if item["surface"] == target.surface
            ),
            None,
        )
        return next((ref for ref, window in state.windows.items() if window == native), None)

    def _image(self, state: AppState, observation: AppObservation) -> None:
        """Register a window image and track its currentness and delivery.

        Args:
            state: Handle state receiving the delivered-capture marker.
            observation: Window capture, scope, and directory used for image metadata.
        """
        images = self.adapters
        image = cast("WindowImage", observation.image).image
        weak_state, weak_image = weakref.ref(state), weakref.ref(image)
        weak_controller = weakref.ref(self)
        session, capture = observation.target.session, cast("WindowImage", observation.image).target.capture
        # A pixel digest can carry delivery knowledge to a fresh capture of the
        # same window. The fresh capture retains its own mapping and input target.
        pixels = (
            window_binding(cast("WindowImage", observation.image).target),
            hashlib.sha256(image).digest(),
            images.image_delivery_epoch(),
        )
        if state.delivered_pixels == pixels:
            state.delivered = (capture, images.image_delivery_epoch())

        def current() -> bool:
            """Check that this callback still refers to the handle's live capture."""
            owner, pixels, controller = weak_state(), weak_image(), weak_controller()
            return (
                owner is not None
                and pixels is not None
                and controller is not None
                and not owner.capture_expired
                and owner.observation is not None
                and cast("AppObservation", owner.observation).image is not None
                and cast("AppObservation", owner.observation).image.image is pixels
                and session == controller.transport.session
            )

        def delivered() -> None:
            if current():
                weak_state().delivered = (capture, images.image_delivery_epoch())
                weak_state().delivered_pixels = (pixels[0], pixels[1], images.image_delivery_epoch())

        selected = next(
            item for item in observation.source_directory["surfaces"] if item["surface"] == observation.target.surface
        )
        from computer_use.interaction.read.presentation.content import json_value, template

        images.register_image(
            image,
            dict(
                kind="window",
                selector=state.selector,
                context=template("Window: {}, App: {}.", json_value(selected["title"]), json_value(state.name)),
                observed=cast("WindowImage", observation.image).observed,
                window=self._window_ref(state, observation.target),
                current=current,
                delivered=delivered,
            ),
        )

    def appRef(self, owner: object, coordinates: bool = False) -> Binding:
        """Resolve the current window binding or its delivered coordinate capture.

        Args:
            owner: Handle whose current observation supplies the target.
            coordinates: Require a current image and, when configured, confirmed delivery.

        Returns:
            Window binding for semantic input, or capture binding for image coordinates.
        """
        state = self.state(owner)
        observed = cast("AppObservation", state.observation)
        if observed is None:
            raise localFailure("this App has no current window observation", "call App.read first", "stale_target")
        self.validateElement(observed.target)
        if not coordinates:
            return window_binding(observed.target)
        if observed.image is None or state.capture_expired:
            raise localFailure(
                "this App has no current window image",
                "read the App and inspect capture availability",
                "snapshot_expired",
            )
        if self.adapters.image_delivery_required() and state.delivered != (
            cast("WindowImage", observed.image).target.capture,
            self.adapters.image_delivery_epoch(),
        ):
            raise localFailure(
                "current window image has not been returned to the model",
                "return the window image, then choose coordinates in the next call",
                "input_unavailable",
            )
        return cast("WindowImage", observed.image).target

    def keyboardTarget(self, owner: object) -> dict[str, Binding]:
        return dict(appRef=self.appRef(owner))

    def checkCoordinates(self, owner: object, x: float, y: float, operation: str = "click") -> None:
        self.appRef(owner, True)
        dimensions = captureMetadata(
            cast("WindowImage", cast("AppObservation", self.state(owner).observation).image).image
        )
        if not finite(x) or not finite(y) or not 0 <= x < dimensions["width"] or not 0 <= y < dimensions["height"]:
            raise localFailure(
                "coordinates are outside this App's observed PNG", "use window-image pixels for " + operation
            )

    async def act(self, method: str, params: Record, owner: object | None = None) -> ActionResult:
        """Translate a desktop/window operation into the session's write request.

        Args:
            method: Requested pointer or keyboard operation.
            params: Operation parameters and the observed desktop/window reference.
            owner: App/Window handle for scoped input, or None for desktop input.

        Returns:
            Execution outcome from the write pipeline.
        """
        if owner is None:
            ref = self.desktop.target(coordinates=method in ("click", "scroll", "drag"))
            if params.get("desktopRef") != ref:
                raise localFailure("desktop observation changed", "read the desktop again", "stale_target", "Desktop")
            observed = self.desktop.observed(ref)
            target = Binding(observed["binding"]["session"], None, None, None, capture=ref)
            parameter = "desktopRef"
        else:
            target, parameter = params["appRef"], "appRef"
        intent = dict(typeText="insert", pressKey="key").get(method, method)
        return await self._execute(
            owner,
            Intent(intent, {k: v for k, v in params.items() if k != parameter}),
            target,
            app_keyboard=owner is not None and method in ("typeText", "paste", "pressKey"),
        )

    async def actElement(
        self,
        owner: object,
        snapshot: ElementSnapshot,
        query: Record | None,
        intent: str,
        parameters: Record,
    ) -> ActionResult:
        """Submit an element action against its binding or a deferred lookup.

        Args:
            owner: Handle constraining the operation's native scope.
            snapshot: Previously observed element binding.
            query: Optional path and timeout used to resolve the element at execution.
            intent: Operation name, such as insert, replace, or click.
            parameters: Parameters for the requested operation.

        Returns:
            Execution outcome from the write pipeline.
        """
        if query is None:
            target, path = snapshot["binding"], None
        else:
            target, path = self._query_root(owner), [dict(segment) for segment in query["path"]]
        return await self._execute(
            owner,
            Intent(intent, parameters),
            target,
            path=path,
            query_timeout_ms=query["timeoutMs"] if query is not None else 0,
        )

    async def _execute(
        self,
        owner: object | None,
        intent: Intent,
        target: OperationTarget,
        *,
        path: LookupPath | None = None,
        query_timeout_ms: int = 0,
        app_keyboard: bool = False,
        forward_current: bool = False,
    ) -> ActionResult:
        return await self.run(
            ExecuteAction(owner, intent, target, path, query_timeout_ms, app_keyboard, forward_current)
        )

    def _query_root(self, owner: object | None) -> Binding | None:
        """Return the handle's live, observed accessibility root for path lookup."""
        target = self.state(owner).query_target
        if target is None:
            raise localFailure("this App has no observed query root", "call App.read first", "stale_target")
        self.validateElement(target)
        if target.node is None:
            raise localFailure(
                "this window has no accessible query root", "use its observed window image", "observation_incomplete"
            )
        return target

    async def find(self, owner: object, path: LookupPath, timeoutMs: int, enabled: bool) -> ElementSnapshot:
        return await self.run(FindElement(owner, path, timeoutMs, enabled))

    async def readElement(
        self,
        owner: object,
        snapshot: ElementSnapshot,
        query: Record | None,
        offset: int,
        previous: ElementSnapshot | None = None,
        *,
        cursor: str | None = None,
    ) -> tuple[Observation, ElementSnapshot]:
        return await self.run(ReadElement(owner, snapshot, query, offset, previous, cursor))

    async def readText(self, owner: object, snapshot: ElementSnapshot, options: Record) -> TextPage:
        return await self.run(ReadText(owner, snapshot, options))


if TYPE_CHECKING:
    from computer_use.interaction.read.model import AppObservation
if TYPE_CHECKING:
    from computer_use.interaction.records import InteractionResult

if TYPE_CHECKING:
    from computer_use.interaction.agent.requests import (
        ActOnElement,
        DiscoverApps,
        ObserveDesktop,
        ObserveElement,
        ObserveText,
        ObserveUI,
        ResolveElement,
        UIKeyboard,
        UIPointer,
    )
    from computer_use.types import AppInfo
if TYPE_CHECKING:
    from computer_use.interaction.records import NativeApplicationSummary
