# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fault injection across the existing native scope, receipt owner and Gate.

These are mechanism regressions, not native desktop or business acceptance.
The fake port returns/loses one receipt; production Passes make every decision.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import (
    BoundWrite,
    OperationSpec,
    PlannedUnit,
    RecordedStep,
    ResolutionMemory,
    ResolvedTarget,
    ResolvedWrite,
    RoutePlan,
)
from computer_use.interaction.write.passes.record_step_outcome import stage as record_step
from computer_use.interaction.write.passes.write_replan_gate import stage as replan
from computer_use.interaction.write.progress import WriteRunState
from computer_use.interaction.write.routes.native_unit.pipeline import NativeUnitPipeline
from computer_use.native.contracts import (
    Binding,
    Channel,
    Cleanup,
    Confirmation,
    Dispatch,
    Facts,
    HostPolicy,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
)
from computer_use.pipeline import Continue, Done, Sequence


def scenario(*, prepare=False, remaining=lambda: 1234):
    target = Binding("session", "instance", "root", "surface", "editor")
    intent = Intent("key", {"key": "super+s"})
    primitive = Primitive(
        "observed-key-route",
        "key_sequence",
        Channel.KEYBOARD,
        Knowledge.YES,
        readiness=Knowledge.UNKNOWN if prepare else Knowledge.YES,
        pending=("receiver",) if prepare else (),
    )
    spec = OperationSpec(intent, target, (), HostPolicy(allow_global_input=False))
    facts = Facts(
        target, "fresh-observation", "same visible name", Knowledge.YES, Knowledge.YES, Knowledge.YES, (), (primitive,)
    )
    resolved = ResolvedWrite(
        BoundWrite(spec, "original-operation", remaining),
        ResolvedTarget(spec, facts, (), False),
        ResolutionMemory(),
    )
    plan = RoutePlan(resolved, primitive, prepare, False)
    return PlannedUnit(plan, plan.command())


def chain(native, state):
    return Sequence(
        name="NativeReceiptGateRegression",
        steps=[NativeUnitPipeline(native, state), record_step(native=native, state=state), replan(state=state)],
    )


def port(**options):
    return SimpleNamespace(execute=AsyncMock(**options), calls=SimpleNamespace(retired=False))


@pytest.mark.parametrize("dispatch,effect", [(Dispatch.UNKNOWN, "possible"), (Dispatch.PARTIAL, "partial")])
def test_uncertain_input_ends_at_gate_and_retains_preparation(dispatch, effect):
    unit, state = scenario(), WriteRunState()
    step = StepResult(unit.command.target, dispatch, "injected uncertain input", preparation=("receiver prepared",))
    native = port(return_value=step)
    transition = asyncio.run(chain(native, state).arun(unit))
    assert isinstance(transition, Done)
    assert transition.reason.kind == "stop"
    assert transition.reason.error.effect == effect
    assert transition.reason.error.code == "execution_unknown"
    assert state.steps == [RecordedStep(unit.command, step)]
    assert state.preparation == ["receiver prepared"]
    assert state.dispatched == dispatch and not state.in_flight
    native.execute.assert_awaited_once_with("original-operation", 1, unit.command, 1234)


def test_dispatched_without_effect_confirmation_is_not_promoted_to_verified():
    unit, state = scenario(), WriteRunState()
    native = port(
        return_value=StepResult(unit.command.target, Dispatch.DISPATCHED, "sent", confirmation=Confirmation.UNAVAILABLE)
    )
    transition = asyncio.run(chain(native, state).arun(unit))
    assert isinstance(transition, Done)
    assert transition.value["status"] == "dispatched"
    native.execute.assert_awaited_once()


@pytest.mark.parametrize("field", ["session", "instance", "root_window", "surface", "node"])
def test_changed_native_identity_is_retired_without_retargeting(field):
    unit, state = scenario(), WriteRunState()
    wrong = replace(unit.command.target, **{field: "replacement-with-same-visible-name"})
    step = StepResult(wrong, Dispatch.DISPATCHED, "wrong-target receipt", preparation=("prepared",))
    native = port(return_value=step)
    with pytest.raises(ValueError, match="different target"):
        asyncio.run(chain(native, state).arun(unit))
    assert native.calls.retired and state.dispatched == Dispatch.UNKNOWN
    assert state.steps == [RecordedStep(unit.command, step)]
    assert state.preparation == ["prepared"]
    native.execute.assert_awaited_once_with("original-operation", 1, unit.command, 1234)
    assert unit.command.target != wrong


@pytest.mark.parametrize("cleanup", [Cleanup.UNKNOWN, Cleanup.FAILED])
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
def test_cleanup_failure_cannot_erase_dispatch_or_trigger_another_route(cleanup, dispatch):
    unit, state = scenario(), WriteRunState()
    step = StepResult(unit.command.target, dispatch, "cleanup not complete", cleanup=cleanup)
    native = port(return_value=step)
    with pytest.raises(ValueError, match="cleanup was not confirmed"):
        asyncio.run(chain(native, state).arun(unit))
    assert native.calls.retired and state.dispatched == dispatch
    assert state.steps == [RecordedStep(unit.command, step)]
    native.execute.assert_awaited_once()


@pytest.mark.parametrize("prepare", [False, True])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, TimeoutError, BrokenPipeError])
def test_cancel_timeout_or_lost_receipt_retires_inflight_scope(prepare, error_type):
    unit, state = scenario(prepare=prepare), WriteRunState()
    error = error_type("injected after entering native call")
    native = port(side_effect=error)
    with pytest.raises(error_type) as caught:
        asyncio.run(chain(native, state).arun(unit))
    assert caught.value is error and native.calls.retired and not state.in_flight
    assert state.steps == [RecordedStep(unit.command, failure_effect="possible")]
    assert state.preparation == (["unknown"] if prepare else [])
    assert state.dispatched == (Dispatch.NOT_DISPATCHED if prepare else Dispatch.UNKNOWN)
    native.execute.assert_awaited_once()


def test_explicit_predispatch_failure_preserves_no_input_evidence():
    unit, state = scenario(), WriteRunState()
    error = ComputerUseError(
        dict(code="stale_target", target="editor", detail="destroyed", effect="none")
    )
    native = port(side_effect=error)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(chain(native, state).arun(unit))
    assert caught.value is error
    assert not native.calls.retired and not state.in_flight
    assert state.dispatched == Dispatch.NOT_DISPATCHED and not state.preparation
    native.execute.assert_awaited_once()


@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
def test_native_receipt_cannot_authorize_retry_after_possible_input(dispatch):
    with pytest.raises(ValueError, match="cannot authorize retry"):
        StepResult(scenario().command.target, dispatch, "contradictory receipt", retry_reason="routeUnavailable")


def test_precondition_refresh_is_bounded_and_keeps_original_target_and_budget():
    budgets = iter((1200, 600))
    unit, state = scenario(remaining=lambda: next(budgets)), WriteRunState()
    step = StepResult(
        unit.command.target,
        Dispatch.NOT_DISPATCHED,
        "changed",
        error_code="input_unavailable",
        retry_reason="preconditionChanged",
    )
    native = port(return_value=step)
    pipeline = chain(native, state)
    first = asyncio.run(pipeline.arun(unit))
    assert isinstance(first, Continue) and first.value is unit.plan.resolved.bound
    outcome = SimpleNamespace(command=unit.command, step=step)
    state.consume(outcome, first.reason)
    second = asyncio.run(pipeline.arun(unit))
    assert isinstance(second, Done) and second.reason.kind == "stop"
    assert state.attempts.precondition_changes == 1
    assert [call.args[3] for call in native.execute.await_args_list] == [1200, 600]
    assert [call.args[1] for call in native.execute.await_args_list] == [1, 2]
    assert all(call.args[2] is unit.command for call in native.execute.await_args_list)
