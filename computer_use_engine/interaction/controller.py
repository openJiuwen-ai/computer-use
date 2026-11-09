# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Own one native session and connect public handles to its execution pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, cast, overload

from computer_use.diagnostics import diagnostic, local_failure
from computer_use.interaction.pipeline import SessionPipeline
from computer_use.interaction.read.acquisition.images.desktop.service import NativeDesktopReader
from computer_use.interaction.read.acquisition.reader import captured_children, captured_field
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.pages import ContentPages
from computer_use.interaction.requests import ComputerInput, ExecuteAction, FindElement
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.scopes import RunningApplication
from computer_use.native.transport import ConnectionPort
from computer_use.pipeline import Control
from computer_use.protocol.observation import NativeNode
from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION
from computer_use.session.bindings import SessionBindings
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import observations_invalidated
from computer_use.session.observation_data import observation_data
from computer_use.session.operations import current_operation_budget, operation_budget

if TYPE_CHECKING:
    from computer_use.interaction.model import ControllerAdapters
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import ElementPath as LookupPath
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.native.contracts import OperationTarget
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import Record
    from computer_use.session.state import AppState
    from computer_use.types import ActionResult, Observation, TextPage, WindowCatalogue


def _observation_completion(result, error):
    """Describe captured completeness without materializing observation text."""
    values = vars(error) if error is not None else result if isinstance(result, Mapping) else {}
    status = values.get("observation_status")
    gap = values.get("observation_error")
    observation = values.get("observation") if status is not None else result
    metadata = observation_data(observation) if observation is not None else None
    if status is None and metadata is None:
        return {}
    summary = {}
    if status is not None:
        summary["observationStatus"] = status
    if gap is not None:
        summary["observationErrorCode"] = gap.get("code")
    if metadata is not None:
        codes = tuple(item.code for item in metadata["gaps"])
        missing = any(record["required"] and record["missing"] for record in metadata["window_coverage"])
        summary.update(
            observationPartial=metadata["partial"],
            contentAvailable=metadata["content_available"],
            observationGapCodes=codes,
            observationComplete=(
                not metadata["partial"]
                and metadata["content_available"]
                and not codes
                and not missing
                and gap is None
                and status not in ("partial", "unavailable")
            ),
        )
    elif status != "not_requested":
        summary["observationComplete"] = status == "available" and gap is None
    return summary


class SessionController:
    """Own native I/O, handle bindings, and execution services for a Computer.

    All requests enter SessionPipeline. The controller supplies its child flows
    with observation readers, input execution, retained content, and callbacks
    that construct public SDK results.
    """

    def __init__(
        self, port: NativePort | None = None, *, adapters: ControllerAdapters, allow_global_input: bool = True
    ) -> None:
        """Create the session services and their shared execution entry.

        Args:
            port: Native I/O port to use, or None to create the configured connection.
            adapters: Public-result constructors and image-delivery hooks supplied
                by the SDK host.
        """
        self.adapters = adapters
        policy = HostPolicy(allow_global_input=allow_global_input)
        self._observation_state = adapters.observation_state
        self._action_result, self._captured_text = adapters.action_result, adapters.captured_text
        self.bindings = SessionBindings()
        self.desktop = DesktopStore(
            DesktopImageAccess(
                adapters.register_image, adapters.image_delivery_epoch, adapters.image_delivery_required,
                retain_delivered_point=lambda: getattr(self.port, "platform", None) == "windows",
            )
        )
        self.transport = (
            port.connection
            if port is not None
            else Connection(
                self.invalidate, protocol_version=NATIVE_PROTOCOL_VERSION, allow_global_input=policy.allow_global_input
            )
        )
        self.port = port or ConnectionPort(self.transport, policy)
        self.reader, self.desktop_reader = NativeAppReader(self.port), NativeDesktopReader(self.port)
        self.engine, self.pages = Engine(self.port), ContentPages()
        self.pipeline = SessionPipeline(self)
        self.interfaces = self.pipeline.interfaces

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
    async def run(self, request: DiscoverWindows) -> WindowCatalogue:
        pass

    @overload
    async def run(self, request: ComputerInput | ExecuteAction | ActOnElement | UIKeyboard | UIPointer) -> ActionResult:
        pass

    async def run(self, request: InteractionRequest) -> InteractionResult:
        owner = current_operation_budget() is None
        with operation_budget() as budget:
            request_type = type(request).__name__
            if owner:
                diagnostic(
                    {
                        "kind": "sdk_operation_start",
                        "operationId": budget.operation_id,
                        "operationType": request_type,
                        "budgetMs": budget.timeout_ms,
                        "targetMs": budget.target_ms,
                    }
                )
            result = None
            error = None
            try:
                control = Control(remaining=budget.remaining, cancelled=budget.cancelled, call_id=budget.operation_id)
                result = await self.pipeline.arun(request, control)
                return result
            except BaseException as caught:
                error = caught
                raise
            finally:
                if owner:
                    # The actual outcome remains authoritative. Expiry after
                    # dispatch/receipt capture must not turn it into no input.
                    try:
                        completion = _observation_completion(result, error)
                    except Exception as summary_error:  # noqa: BLE001 - diagnostics cannot replace captured effects
                        completion = {"observationDiagnosticError": type(summary_error).__name__}
                    diagnostic(
                        {
                            "kind": "sdk_operation_end",
                            "operationId": budget.operation_id,
                            "operationType": request_type,
                            "durationMs": budget.elapsed_ms,
                            "budgetMs": budget.timeout_ms,
                            "targetMs": budget.target_ms,
                            "targetExceeded": budget.target_exceeded,
                            "overrun": budget.expired,
                            "status": "error" if error is not None else "ok",
                            **(
                                {"errorType": type(error).__name__, "effect": getattr(error, "effect", None)}
                                if error is not None
                                else {"outcome": result.get("status") if isinstance(result, Mapping) else None}
                            ),
                            **completion,
                        }
                    )

    def register(self, owner: object, scope: Record) -> None:
        self.bindings.register(owner, scope)
        if scope.get("_nativeState") is None:
            state = self.bindings.state(owner)
            retained = self.interfaces.windows.application_for_selector(self.transport.session, state.selector)
            if retained is not None:
                state.running = RunningApplication(retained.selector, retained.name, retained.session)
                state.directory, state.refs = retained.directory, retained.refs

    def state(self, owner: object) -> AppState:
        return self.bindings.state(owner)

    def scope(self, owner: object) -> Record:
        return self.bindings.scope(owner)

    def invalidate(self) -> None:
        self.desktop.reset()
        if hasattr(self, "interfaces"):
            self.interfaces.windows.clear_lookup_caches()
        observations_invalidated(self)
        # Handles retain their prior session identity so subsequent validation
        # detects expiration.

    def validate_element(self, target: Binding) -> None:
        self.bindings.validate_element(target, self.transport.session)

    def remember_observation(self, state: AppState, target: Binding, tree: SemanticSnapshot) -> None:
        """Retain observed element identities and continuations for later SDK calls."""
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
        outside = set()
        if tree.native.backend == "windows":
            for token, node in tree.native.nodes.items():
                if any(
                    supplement.key == "observation_scope"
                    and isinstance(supplement.value, Mapping)
                    and supplement.value.get("ownership") == "outside"
                    for supplement in node.supplements
                ):
                    outside.add(token)
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

        def text_source(ref: str, token: str) -> str | None:
            """Use the acquisition's explicit readText source without changing input values."""
            if tree.collection is not None:
                path = tree.collection.text_read_fields.get(token)
                if path:
                    return ".".join(map(str, path))
            return tree.value_fields.get(ref)

        return cast(
            "dict[str, ElementSnapshot]",
            {
                ref: dict(
                    container=ordinary(node, tree.native.nodes.get(node)),
                    binding=replace(target, node=node, capture=None),
                    scope_kind=tree.scope_kind,
                    source=dict(ref=ref, observed=tree.facts["observed"]),
                    text=captured_field(tree, ref, text_source(ref, node), nodes=native_nodes),
                    children=captured_children(tree, ref, relations=child_relations),
                    **({"native_window_outside": True} if node in outside else {}),
                )
                for ref, node in tree.refs.items()
            },
        )

    async def act(self, method: str, params: Record, owner: object | None = None) -> ActionResult:
        """Translate a desktop/window operation into the session's write request.

        Args:
            method: Requested pointer or keyboard operation.
            params: Operation parameters and the observed desktop/window reference.
            owner: App/window handle for scoped input, or None for desktop input.

        Returns:
            Execution outcome from the write pipeline.
        """
        if owner is None:
            ref = self.desktop.target(coordinates=method in ("click", "scroll", "drag"))
            if params.get("desktopRef") != ref:
                raise local_failure("desktop observation changed", "stale_target", "Desktop")
            observed = self.desktop.observed(ref)
            target = Binding(observed["binding"]["session"], None, None, None, capture=ref)
            parameter = "desktopRef"
        else:
            target, parameter = params["appRef"], "appRef"
        if owner is None and method in ("click", "scroll", "drag"):
            params = self.desktop.map_parameters(method, params)
        intent = dict(type_text="insert", press_key="key").get(method, method)
        return await self._execute(
            owner,
            Intent(intent, {k: v for k, v in params.items() if k != parameter}, global_input=owner is None),
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
            raise local_failure("this App has no observed query root", "stale_target")
        self.validate_element(target)
        if target.node is None:
            raise local_failure("this window has no accessible query root", "observation_incomplete")
        return target

    async def find(self, owner: object, path: LookupPath, timeout_ms: int, enabled: bool) -> ElementSnapshot:
        return await self.run(FindElement(owner, path, timeout_ms, enabled))


if TYPE_CHECKING:
    from computer_use.interaction.records import InteractionResult

if TYPE_CHECKING:
    from computer_use.interaction.agent.requests import (
        ActOnElement,
        DiscoverApps,
        DiscoverWindows,
        ObserveDesktop,
        ObserveElement,
        ObserveText,
        ObserveUI,
        ResolveElement,
        UIKeyboard,
        UIPointer,
    )
    from computer_use.types import AppInfo
