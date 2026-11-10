# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The write main pipeline: fixed intent, resolution, one route and a Gate."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.identity import FrozenRecord, freeze_result
from computer_use.interaction.control import scope_control
from computer_use.interaction.write.impact import action_impact, invoked_ax_menu_target
from computer_use.interaction.write.model import OpenedWrite
from computer_use.interaction.write.passes.complete_empty_input import stage as complete_empty_input
from computer_use.interaction.write.passes.format_write_result import stage as format_write_result
from computer_use.interaction.write.passes.observe_selected_pair import stage as observe_selected_pair
from computer_use.interaction.write.passes.parse_write_intent import stage as parse_write_intent
from computer_use.interaction.write.passes.pin_operation_target import stage as pin_operation_target
from computer_use.interaction.write.passes.recognize_existing_completion import stage as recognize_existing_completion
from computer_use.interaction.write.passes.record_resolution import stage as record_resolution
from computer_use.interaction.write.passes.record_step_outcome import stage as record_step_outcome
from computer_use.interaction.write.passes.resolve_requested_path import stage as resolve_requested_path
from computer_use.interaction.write.passes.snapshot_resolution_input import stage as snapshot_resolution_input
from computer_use.interaction.write.passes.write_replan_gate import stage as write_replan_gate
from computer_use.interaction.write.progress import WriteRunState, failure
from computer_use.interaction.write.resolution.pipeline import TargetResolutionPipeline
from computer_use.interaction.write.routes.pipeline import RouteExecutionPipeline, RouteSelectionPipeline
from computer_use.native.contracts import Dispatch
from computer_use.pipeline import Done, Repeat, Scope, Select, Sequence

if TYPE_CHECKING:
    from typing import AsyncIterator

    from computer_use.interaction.records import ElementPath as LookupPath
    from computer_use.interaction.records import Remaining
    from computer_use.interaction.write.model import AttemptOutcome, BoundWrite, WriteRequest
    from computer_use.native.contracts import Intent, OperationTarget
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Continue, Control
    from computer_use.protocol.records import Record


class WritePipeline:
    """Execute a write against its resolved native target.

    Resolve the requested path or current receiver, then choose and execute a
    native route. Each request owns the attempt history and accumulated effects;
    the Gate uses them to return a receipt or continue within the original budget.

    Attributes:
        native: Driver port supplying operation leases, target facts, and execution.
        state: Attempt history, resolution memory, and native effects for this write.
        intent: Input parser producing the bounded write request.
        resolution: Target and receiver resolver used by each permitted attempt.
        main: Composed operation scope, attempt loop, and receipt delivery flow.
    """

    def __init__(self, native: NativePort) -> None:
        self.native, self.state = native, WriteRunState()
        self.completed_receipt: Record | None = None
        self.app_change_baseline = None
        self.app_change_input_baseline = None
        self.intent = Sequence(name="IntentPipeline", steps=[parse_write_intent()])
        self.resolution = TargetResolutionPipeline(native)
        self.main = self.build()

    def build(self) -> Sequence:
        routes = Sequence(
            name="SelectedRoutePipeline",
            steps=[
                RouteSelectionPipeline(self.native, self.state),
                RouteExecutionPipeline(self.native, self.state),
            ],
        )
        completed = Select(
            name="ExistingCompletionOrRoute",
            selector=recognize_existing_completion(),
            branches={
                "empty": complete_empty_input(),
                "route": Sequence(
                    name="ObserveAndSelectRoute", steps=[observe_selected_pair(state=self.state), routes]
                ),
            },
        )
        body = Sequence(
            name="WriteAttemptPipeline",
            steps=[
                snapshot_resolution_input(state=self.state),
                self.resolution,
                record_resolution(state=self.state),
                completed,
                record_step_outcome(native=self.native, state=self.state),
            ],
        )
        repeat = Repeat(
            name="WriteReplanLoop",
            body=body,
            gate=write_replan_gate(state=self.state),
            on_decision=self.consume_decision,
            bounded_by="existing total deadline, semantic RouteKeys and shared Attempts limits",
        )
        binding = Sequence(
            name="TargetBindingPipeline",
            steps=[
                resolve_requested_path(native=self.native),
                pin_operation_target(native=self.native, state=self.state),
            ],
        )
        return Sequence(
            name="WritePipeline",
            steps=[
                Scope(
                    name="NativeOperationScope",
                    using=self.operation_scope,
                    body=Sequence(name="BoundWritePipeline", steps=[binding, repeat]),
                ),
                format_write_result(),
            ],
        )

    async def run_in_call(
        self,
        intent: Intent,
        target: OperationTarget,
        remaining: Remaining,
        *,
        path: LookupPath | None = None,
        query_timeout_ms: int = 0,
        forward_current: bool = False,
        _control: Control | None = None,
    ) -> Record:
        """Execute a parsed write within the caller's native claim and remaining budget.

        Args:
            intent: Requested operation and its validated parameters.
            target: Original native identity or observed application installation.
            remaining: Milliseconds left on the enclosing call; raises on expiry.
            path: Optional semantic lookup components inside the target.
            query_timeout_ms: Bounded lookup wait in milliseconds.
            forward_current: Whether to address the selected container's current receiver.
            _control: Parent call metadata and diagnostic scope.

        Returns:
            The completed receipt with dispatch or verification status.
        """
        self.native.calls.validate_claim(remaining)
        control = (
            self.native.calls.control(remaining)
            if _control is None
            else scope_control(self.native, remaining, _control)
            if _control.call_id is None
            else _control.with_remaining(remaining)
        )
        request = self.intent.run((intent, target, path, query_timeout_ms, forward_current), control)
        try:
            result = await self.main.arun(request, control)
            receipt = {**result, "impact": action_impact(self.state, self.native.policy)}
            if self.app_change_baseline is not None:
                receipt["appChangeBaseline"] = self.app_change_baseline
            if self.app_change_input_baseline is not None:
                receipt["appChangeInputBaseline"] = self.app_change_input_baseline
            return receipt
        except BaseException as caught:
            if isinstance(caught, asyncio.CancelledError):
                error = caught
            elif isinstance(caught, ComputerUseError):
                error = failure(
                    caught.code,
                    caught.target,
                    caught.detail,
                    self.state.dispatched,
                    self.state.preparation,
                    minimum_effect=(
                        "possible" if caught.effect == "none" and self.state.native_mutation_possible else caught.effect
                    ),
                    # Internal failure metadata requests observation without changing the public error.
                    # pylint: disable-next=protected-access
                    observation_reason=caught._observation_reason,
                )
                error.scope_cleanup = caught.scope_cleanup
            else:
                error = failure(
                    "execution_unknown" if self.state.dispatched != Dispatch.NOT_DISPATCHED else "execution_failed",
                    "desktop",
                    str(caught),
                    self.state.dispatched,
                    self.state.preparation,
                    minimum_effect="possible" if self.state.native_mutation_possible else "none",
                )
            error.impact = freeze_result(action_impact(self.state, self.native.policy))
            if isinstance(error, ComputerUseError) and intent.kind == "openMenu":
                # Internal dispatch evidence preserves the invoked menu target for failure observation.
                # pylint: disable-next=protected-access
                error._invoked_menu_target = invoked_ax_menu_target(self.state)
            if self.app_change_baseline is not None:
                error.app_change_baseline = self.app_change_baseline
            if error is caught:
                raise
            if self.completed_receipt is not None and self.native.calls.retired:
                # The Gate accepted the original input before finish failed.
                # Keep resource finalization separate from the input cleanup.
                result = self.completed_receipt
                error.action_receipt = FrozenRecord(
                    {
                        "status": result["status"],
                        "target": result["target"],
                        "detail": result["detail"],
                        "intent": request.intent.kind,
                        "impact": error.impact,
                    }
                )
                error.scope_cleanup = "unknown"
            for note in getattr(caught, "__notes__", ()):
                error.add_note(note)
            raise error from caught

    @asynccontextmanager
    async def operation_scope(self, request: WriteRequest, control: Control) -> AsyncIterator[OpenedWrite]:
        """Open the native operation lease for the parsed write request."""
        async with self.native.calls.operation(
            request.target, cast("Callable[[], int]", control.remaining), intent=request.intent
        ) as lease:
            self.app_change_baseline = lease.app_change_baseline
            yield OpenedWrite(request, lease)

    def consume_decision(
        self, outcome: AttemptOutcome, transition: Continue[BoundWrite] | Done[Record], _: object
    ) -> None:
        """Apply the Gate's transition to this write's shared attempt state."""
        sampled = outcome.step.app_change_input_baseline if outcome.step is not None else None
        if sampled is not None and self.app_change_baseline is not None and all(
            sampled[key] == self.app_change_baseline[key] for key in ("session", "application", "incarnation")
        ):
            # Requested input is later than context preparation. Keep its first
            # native barrier stamp, rather than treating preparation callbacks
            # as evidence that this input has already been observed.
            if self.app_change_input_baseline is None:
                self.app_change_input_baseline = sampled
        self.state.consume(outcome, transition.reason)
        if isinstance(transition, Done) and self.state.dispatched == Dispatch.DISPATCHED:
            self.completed_receipt = transition.value


if TYPE_CHECKING:
    from typing import Callable
