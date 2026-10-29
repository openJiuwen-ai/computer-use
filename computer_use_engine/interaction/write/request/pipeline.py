# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate and reconcile an API action before executing and publishing its receipt."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.operation import HostOperation
from computer_use.interaction.query.pipeline import AppScopeReconciliation
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.interaction.write.request.passes.materialize_action_result import stage as materialize_action_result
from computer_use.interaction.write.request.passes.prepare_write_scope import stage as prepare_write_scope
from computer_use.interaction.write.request.passes.publish_action_event import stage as publish_action_event
from computer_use.interaction.write.request.passes.select_write_scope import stage as select_write_scope
from computer_use.interaction.write.request.passes.validate_write_binding import stage as validate_write_binding
from computer_use.pipeline import Contract, Effect, Scope, Select, Sequence
from computer_use.pipeline.core import Node
from computer_use.session.events import pending_observations_discarded

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
        try:
            options: Record = dict(path=request.path, query_timeout_ms=request.query_timeout_ms, _control=control)
            if request.forward_current:
                options["forward_current"] = True
            if self.borrowed:
                return await engine.execute_in_call(
                    request.intent, cast("OperationTarget", plan.target), cast("Remaining", plan.remaining), **options
                )
            return await engine.execute(request.intent, cast("OperationTarget", plan.target), **options)
        finally:
            self.children = ()

    def _run(self, value: ActionPlan, control: Control) -> Record:
        raise TypeError("Writes require arun()")


class WriteFailureBoundary(Node["ExecuteAction", "ActionResult"]):
    """Reconcile observation state after a failed write.

    Run the enclosed action flow. On failure, discard pending observations,
    reset desktop-action state or expire a matching stale App capture as
    applicable, and propagate the error.

    Attributes:
        controller: Owns the desktop, App observations, and pending delivery state.
        body: Enclosed action flow whose failures require observation cleanup.
    """

    def __init__(self, controller: SessionController, body: Step[ExecuteAction, ActionResult]) -> None:
        (self.controller, self.body, self.children) = (controller, body, (body,))
        super().__init__("WriteFailureBoundary", body.contract)

    async def _arun(self, request: ExecuteAction, control: Control) -> ActionResult:
        """Run the write and reconcile stale image and pending-observation state on failure."""
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
            pending_observations_discarded(self.controller)
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
                validate_write_binding(validate_element=controller.validate_element),
                WriteFailureBoundary(
                    controller,
                    Sequence(
                        name="PrepareAndWrite",
                        steps=[prepare_write_scope(state_of=controller.state), dispatch],
                    ),
                ),
                publish_action_event(),
                # The pipeline shares retained bindings with their owning service.
                # pylint: disable-next=protected-access
                materialize_action_result(action_result=controller._action_result),
            ],
        )


if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import OperationTarget
if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
