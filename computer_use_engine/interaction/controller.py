"""Own one native session and connect public handles to its execution pipeline."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING, cast, overload

from computer_use.diagnostics import local_failure
from computer_use.interaction.pipeline import SessionPipeline
from computer_use.interaction.read.acquisition.images.desktop.service import NativeDesktopReader
from computer_use.interaction.read.acquisition.reader import captured_children, captured_field
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.pages import ContentPages
from computer_use.interaction.requests import ExecuteAction, FindElement
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.observation import NativeNode
from computer_use.protocol.session import desktop_mode, NATIVE_PROTOCOL_VERSION
from computer_use.session.bindings import SessionBindings
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import observations_invalidated

if TYPE_CHECKING:
    from computer_use.interaction.model import ControllerAdapters
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import ElementPath as LookupPath, ElementSnapshot
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.native.contracts import OperationTarget
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import Record
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
            port.connection
            if port is not None
            else Connection(self.invalidate, protocol_version=NATIVE_PROTOCOL_VERSION)
        )
        self.port = port or ConnectionPort(
            self.transport, HostPolicy(desktop_mode(), os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1")
        )
        self.reader, self.desktop_reader = NativeAppReader(self.port), NativeDesktopReader(self.port)
        self.engine, self.pages = Engine(self.port), ContentPages()
        self.pipeline = SessionPipeline(self)
        from computer_use.interaction.agent.pipeline import AgentInterfacePipeline

        self.interfaces = AgentInterfacePipeline(self)

    @overload
    async def run(self, request: ObserveUI | ObserveDesktop | ObserveElement) -> Observation:
        pass

    @overload
    async def run(self, request: ObserveText) -> TextPage:
        pass

    @overload
    async def run(self, request: FindElement | ResolveElement) -> ElementSnapshot:
        pass

    @overload
    async def run(self, request: DiscoverApps) -> list[AppInfo]:
        pass

    @overload
    async def run(self, request: ExecuteAction | ActOnElement | UIKeyboard | UIPointer) -> ActionResult:
        pass

    async def run(self, request: InteractionRequest) -> InteractionResult:
        return await self.pipeline.arun(request)

    def register(self, owner: object, scope: Record) -> None:
        self.bindings.register(owner, scope)

    def state(self, owner: object) -> AppState:
        return self.bindings.state(owner)

    def scope(self, owner: object) -> Record:
        return self.bindings.scope(owner)

    def invalidate(self) -> None:
        self.desktop.reset()
        self.bindings.invalidate()
        observations_invalidated(self)
        # Handles retain their prior session identity so subsequent validation
        # detects expiration.

    def validate_element(self, target: Binding) -> None:
        self.bindings.validate_element(target, self.transport.session)

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
                    text=captured_field(tree, ref, tree.value_fields.get(ref), nodes=native_nodes),
                    children=captured_children(tree, ref, relations=child_relations),
                )
                for ref, node in tree.refs.items()
            },
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
                raise local_failure("desktop observation changed", "read the desktop again", "stale_target", "Desktop")
            observed = self.desktop.observed(ref)
            target = Binding(observed["binding"]["session"], None, None, None, capture=ref)
            parameter = "desktopRef"
        else:
            target, parameter = params["appRef"], "appRef"
        intent = dict(type_text="insert", press_key="key").get(method, method)
        return await self._execute(
            owner,
            Intent(intent, {k: v for k, v in params.items() if k != parameter}),
            target,
            app_keyboard=owner is not None and method in ("type_text", "paste", "press_key"),
        )

    async def act_element(
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
            raise local_failure("this App has no observed query root", "call App.read first", "stale_target")
        self.validate_element(target)
        if target.node is None:
            raise local_failure(
                "this window has no accessible query root", "use its observed window image", "observation_incomplete"
            )
        return target

    # pylint: disable-next=invalid-name
    async def find(self, owner: object, path: LookupPath, timeoutMs: int, enabled: bool) -> ElementSnapshot:
        return await self.run(FindElement(owner, path, timeoutMs, enabled))


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
