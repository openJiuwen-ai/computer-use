# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The addressed write loop preserves task ownership and native progress."""

import asyncio
import sys
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from regression.test_text_input_routes import EDITOR, FOCUS, TARGET, RecordingPort, observed

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.native_unit.pipeline import NativeUnitPipeline
from computer_use.native.contracts import Dispatch, Intent, StepResult


@pytest.mark.parametrize("borrowed", [False, True])
def test_write_attempts_and_operation_cleanup_stay_on_the_callers_task(borrowed):
    context = ContextVar("write_attempt_context")
    diagnostics = []
    native_events = []

    async def run():
        owner = asyncio.current_task()
        token = context.set(object())
        marker = context.get()

        async def checkpoint(name):
            await asyncio.sleep(0)
            assert asyncio.current_task() is owner
            assert context.get() is marker
            native_events.append(name)

        class Port(RecordingPort):
            pipeline_diagnostic = diagnostics.append

            async def begin(self, intent, target, timeout_ms):
                await checkpoint("begin")
                return await super().begin(intent, target, timeout_ms)

            async def inspect(self, operation, target, timeout_ms):
                await checkpoint("inspect")
                return await super().inspect(operation, target, timeout_ms)

            async def execute(self, operation, step, command, timeout_ms):
                await checkpoint(("execute", step, command.prepare))
                return await super().execute(operation, step, command, timeout_ms)

            async def finish(self, operation):
                await checkpoint("finish")
                return await super().finish(operation)

        port = Port(
            [observed(FOCUS), observed(EDITOR)],
            [
                StepResult(TARGET, Dispatch.NOT_DISPATCHED, "prepared"),
                StepResult(TARGET, Dispatch.DISPATCHED, "input sent"),
            ],
        )
        intent = Intent("insert", {"text": "query"})
        try:
            engine = Engine(port)
            if borrowed:
                with port.calls.claim(5000) as remaining:
                    result = await engine.execute_in_call(intent, TARGET, remaining)
            else:
                result = await engine.execute(intent, TARGET)
            assert result["status"] == "dispatched"
            assert not port.calls.busy
        finally:
            context.reset(token)

    asyncio.run(run())

    assert native_events == ["begin", "inspect", ("execute", 1, True), "inspect", ("execute", 2, False), "finish"]
    attempts = [event for event in diagnostics if event.phase in ("attempt", "native_receipt")]
    assert [(event.attempt, event.phase, event.branch, event.dispatch) for event in attempts] == [
        (1, "attempt", None, None),
        (1, "native_receipt", "prepare_receiver", "notDispatched"),
        (2, "attempt", None, None),
        (2, "native_receipt", "native_text_edit", "dispatched"),
    ]
    assert len({event.call_id for event in attempts}) == 1
    assert attempts[0].call_id is not None
    assert all("WriteReplanLoop" in event.path for event in attempts)


def test_native_unit_releases_in_flight_after_retirement_fails():
    dispatch_error = OSError("native dispatch failed")
    retirement_error = RuntimeError("retirement callback failed")
    closed_under = []

    class Calls:
        @property
        def retired(self):
            return False

        @retired.setter
        def retired(self, value):
            assert value and sys.exception() is dispatch_error
            raise retirement_error

    class State:
        @property
        def in_flight(self):
            return self.active

        @in_flight.setter
        def in_flight(self, active):
            self.active = active
            if not active:
                closed_under.append(sys.exception())

    state = State()
    plan = SimpleNamespace(
        resolved=SimpleNamespace(bound=SimpleNamespace(remaining=lambda: 42)),
        click_selection=None,
    )
    unit = SimpleNamespace(plan=plan)
    pipeline = NativeUnitPipeline(SimpleNamespace(calls=Calls()), state)
    with pytest.raises(RuntimeError, match="retirement callback failed") as caught:
        with pipeline.invocation(unit, None) as invocation:
            assert state.in_flight and invocation.budget == 42
            raise dispatch_error

    assert caught.value is retirement_error and caught.value.__context__ is dispatch_error
    assert not state.in_flight and closed_under == [retirement_error]


def test_finish_failure_retains_the_completed_input_receipt():
    failure = BrokenPipeError("finish lost")
    failure.add_note("native cleanup diagnostic")

    class Port(RecordingPort):
        async def finish(self, operation):
            await super().finish(operation)
            raise failure

    port = Port([observed(EDITOR)], [StepResult(TARGET, Dispatch.DISPATCHED, "input sent")])
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(Engine(port).execute(Intent("insert", {"text": "query"}), TARGET))

    error = caught.value
    assert error.__cause__ is failure
    assert (error.code, error.effect, error.scope_cleanup) == ("execution_unknown", "possible", "unknown")
    assert error.action_receipt["status"] == "dispatched"
    assert error.action_receipt["detail"] == "input sent"
    assert error.action_receipt["intent"] == "insert"
    assert error.__notes__ == ["native cleanup diagnostic"]
    assert port.calls.retired and len(port.commands) == 1 and port.finished == ["operation"]
