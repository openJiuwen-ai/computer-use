# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate and reconcile an API action before executing and publishing its receipt."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.identity import FrozenRecord
from computer_use.interaction.operation import HostOperation
from computer_use.interaction.query.pipeline import AppScopeReconciliation
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.interaction.write.request.observation import WriteWithObservation
from computer_use.interaction.write.request.passes.materialize_action_result import stage as materialize_action_result
from computer_use.interaction.write.request.passes.prepare_write_scope import stage as prepare_write_scope
from computer_use.interaction.write.request.passes.publish_action_event import stage as publish_action_event
from computer_use.interaction.write.request.passes.resolve_window_close import stage as resolve_window_close
from computer_use.interaction.write.request.passes.select_write_scope import stage as select_write_scope
from computer_use.interaction.write.request.passes.validate_element_scope import stage as validate_element_scope
from computer_use.interaction.write.request.passes.validate_write_binding import stage as validate_write_binding
from computer_use.native.contracts import Binding
from computer_use.pipeline import Contract, Effect, Scope, Select, Sequence
from computer_use.pipeline.core import Node
from computer_use.session.events import observations_before_write

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from typing import AsyncIterator, Callable

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import ExecuteAction
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult


class ExecuteNativeWrite(Node["ActionPlan", "Record"]):
    """Execute a prepared controller action through the write engine.

    Pass the selected target and lookup options to the engine, borrowing the
    reconciliation budget when one has already been claimed. Return its native
    write receipt for event publication and public-result assembly.

    Attributes:
        controller: Supplies the engine used for this session's action.
        borrowed: Whether the action runs within the plan's existing call claim.
    """

    def __init__(self, controller: SessionController, borrowed: bool = False) -> None:
        (self.controller, self.borrowed) = (controller, borrowed)
        self.children = ()
        super().__init__("ExecuteNativeWrite", Contract(ActionPlan, dict, frozenset(Effect)))

    async def _arun(self, plan: ActionPlan, control: Control) -> Record:
        """Execute the action using either its reconciled call claim or a new engine claim."""
        (engine, request) = (self.controller.engine, plan.request)
        self.children = (engine.pipeline,)
        label = _target_label(request, plan.target)
        intent = plan.mechanism or request.intent
        try:
            options: Record = dict(path=request.path, query_timeout_ms=request.query_timeout_ms, _control=control)
            if request.forward_current:
                options["forward_current"] = True
            if self.borrowed:
                receipt = await engine.execute_in_call(
                    intent, cast("OperationTarget", plan.target), cast("Remaining", plan.remaining), **options
                )
            else:
                receipt = await engine.execute(intent, cast("OperationTarget", plan.target), **options)
            receipt["intent"] = request.intent.kind
            if label:
                receipt["target"] = label
            return receipt
        except ComputerUseError as error:
            # The execution boundary supplies the public-method name for error formatting.
            # pylint: disable-next=protected-access
            error._method = request.intent.kind
            if label:
                error.target = f"{label} / {error.target}"
            if error.action_receipt is not None:
                receipt = error.action_receipt
                error.action_receipt = FrozenRecord(
                    {**receipt, "intent": request.intent.kind, "target": label or receipt.target}
                )
            raise
        finally:
            self.children = ()

    def _run(self, value: ActionPlan, control: Control) -> Record:
        raise TypeError("Writes require arun()")


def _target_label(request: ExecuteAction, target: OperationTarget | None) -> str | None:
    """Describe existing public bindings without querying or changing the target."""
    if request.owner is None:
        return "Computer"
    instance = getattr(request.owner, "_instance", None)
    if instance is None or not isinstance(target, Binding):
        return None
    member = getattr(instance, "members", {}).get(target.root_window)
    if member is None or (member.get("surface"), member.get("instance")) != (target.surface, target.instance):
        # Native root slots may be reused. A current public label cannot be
        # borrowed for an older binding merely because its root string agrees.
        return None
    root = target.root_window
    displayed = request.display_window
    if displayed is not None:
        host = getattr(instance, "members", {}).get(displayed.window)
        if host is not None and (host.get("surface"), host.get("instance")) == (displayed.surface, displayed.instance):
            root = displayed.window
    window = next((ref for ref, key in getattr(instance, "windows", {}).items() if key == root), None)
    if window is None:
        return None
    parts = [f"Window {window}"]
    if request.snapshot is not None:
        element = next(
            (ref for ref, snapshot in getattr(instance, "refs", {}).items() if snapshot is request.snapshot), None
        )
        if element is not None:
            parts.append(f"Element {element}")
    return " / ".join(parts)


class WriteFailureBoundary(Node["ExecuteAction", "ActionResult"]):
    """Publish earlier observations and reconcile a failed input scope.

    Publish previous observations before entering the action. On failure,
    reset desktop-action state or expire a matching stale App capture as
    applicable, and propagate the error without erasing earlier facts.

    Attributes:
        controller: Owns the desktop, App observations, and observation delivery.
        body: Enclosed action flow whose failures reconcile affected input state.
    """

    def __init__(self, controller: SessionController, body: Step[ExecuteAction, ActionResult]) -> None:
        (self.controller, self.body, self.children) = (controller, body, (body,))
        super().__init__("WriteFailureBoundary", body.contract)

    async def _arun(self, request: ExecuteAction, control: Control) -> ActionResult:
        """Order previous facts before input and reconcile only failed input state."""
        observations_before_write(self.controller)
        try:
            return await self.body.arun(request, control)
        except BaseException as error:
            if request.owner is None:
                self.controller.desktop.reset()
            elif (
                isinstance(error, ComputerUseError)
                and error.code == "snapshot_expired"
                and (cast("Binding", request.target).capture is not None)
            ):
                state = self.controller.state(request.owner)
                if (
                    state.observation
                    and state.observation.image
                    and (state.observation.image.target == cast("Binding", request.target))
                ):
                    state.capture_expired = True
            raise

    def _run(self, value: ExecuteAction, control: Control) -> ActionResult:
        raise TypeError("Writes require arun()")


class ControllerWritePipeline(HostOperation):
    """Turn an API action into a public write result.

    Validate the target binding and determine whether its App scope needs
    reconciliation. Execute the action, publish its receipt, and convert that
    receipt into the ActionResult returned to the caller.
    """

    def __init__(self, controller: SessionController) -> None:

        @asynccontextmanager
        async def claim(plan: ActionPlan, control: Control) -> AsyncIterator[ActionPlan]:
            with controller.port.calls.claim(30000) as remaining:
                yield replace(plan, remaining=remaining)

        super().__init__(self.build(controller, claim))

    @staticmethod
    def build(
        controller: SessionController,
        claim: Callable[[ActionPlan, Control], AbstractAsyncContextManager[ActionPlan]],
    ) -> Step[ExecuteAction, ActionResult]:
        dispatch = Select(
            name="AppScopeOrDirectWrite",
            selector=select_write_scope(),
            branches={
                False: ExecuteNativeWrite(controller),
                True: Scope(
                    name="ReconciledWriteCallScope",
                    using=claim,
                    body=Sequence(
                        name="ReconciledWrite",
                        steps=[
                            AppScopeReconciliation(controller, lambda plan: True),
                            ExecuteNativeWrite(controller, borrowed=True),
                        ],
                    ),
                ),
            },
        )
        return Sequence(
            name="ControllerWritePipeline",
            steps=[
                WriteWithObservation(
                    controller,
                    Sequence(
                        name="WriteAndPublishReceipt",
                        steps=[
                            validate_write_binding(validate_element=controller.validate_element),
                            WriteFailureBoundary(
                                controller,
                                Sequence(
                                    name="PrepareAndWrite",
                                    steps=[
                                        validate_element_scope(controller=controller),
                                        prepare_write_scope(state_of=controller.state),
                                        resolve_window_close(controller=controller),
                                        dispatch,
                                    ],
                                ),
                            ),
                            publish_action_event(),
                        ],
                    ),
                ),
                # The pipeline shares retained bindings with their owning service.
                # pylint: disable-next=protected-access
                materialize_action_result(action_result=controller._action_result),
            ],
        )


if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import OperationTarget
