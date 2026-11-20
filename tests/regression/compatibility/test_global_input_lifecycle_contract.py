# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Global input retains dispatch evidence and caller context through cleanup."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.global_input import GlobalInput
from computer_use.interaction.requests import ComputerInput
from computer_use.native.contracts import Dispatch, HostPolicy, Intent, StepResult
from computer_use.session import events
from computer_use.session.results import action_result


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "cleanup_failed"])
def test_global_input_keeps_task_context_and_dispatch_through_cleanup(monkeypatch, outcome):
    seen = []
    context = ContextVar("global-input-owner")
    cleanup_failure = RuntimeError("cleanup failed after dispatch")

    async def run():
        owner = asyncio.current_task()
        context.set("caller")

        def record(stage):
            assert asyncio.current_task() is owner
            seen.append((stage, context.get()))

        @contextmanager
        def claim(timeout):
            assert timeout == 30000
            record("claim")
            try:
                yield lambda: 27000
            finally:
                record("release")

        async def connect(timeout):
            assert timeout == 27000
            record("connect")
            return SimpleNamespace(session="session")

        @asynccontextmanager
        async def operation(scope, remaining, *, intent):
            assert scope.session == "session" and intent.global_input and remaining() == 27000
            record("admit")
            try:
                yield SimpleNamespace(token="lease")
            finally:
                await asyncio.sleep(0)
                record("cleanup")
                if outcome == "cleanup_failed":
                    calls.retired = True
                    raise cleanup_failure

        async def execute(token, scope, intent, timeout):
            assert token == "lease" and timeout == 27000 and intent.parameters["key"] == "t"
            record("dispatch")
            context.set("dispatch-context")
            if outcome == "cancelled":
                asyncio.get_running_loop().call_soon(owner.cancel, "withdraw input")
                await asyncio.Future()
            return StepResult(scope, Dispatch.DISPATCHED, "delivered")

        calls = SimpleNamespace(claim=claim, operation=operation, retired=False)
        port = SimpleNamespace(calls=calls, platform="macos", connect=connect, execute_global=execute, policy=HostPolicy())
        controller = SimpleNamespace(port=port, _action_result=action_result)
        monkeypatch.setattr(events, "_observation_handler", lambda event: record(event["kind"]))
        monkeypatch.setattr(events, "_action_handler", lambda receipt: record(receipt["status"]))
        request = ComputerInput(Intent("key", {"key": "t"}, True))
        return await GlobalInput(controller).arun(request)

    expected = [(stage, "caller") for stage in ("before-write", "claim", "connect", "admit", "dispatch")]
    expected.append(("cleanup", "dispatch-context"))
    if outcome == "completed":
        assert asyncio.run(run())["status"] == "dispatched"
        expected.append(("dispatched", "dispatch-context"))
    elif outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError, match="withdraw input") as caught:
            asyncio.run(run())
        assert caught.value.impact["attempts"][0]["dispatch"] == "unknown"
    else:
        with pytest.raises(ComputerUseError) as caught:
            asyncio.run(run())
        assert caught.value.__cause__ is cleanup_failure and caught.value.effect == "possible"
        attempt = caught.value.impact["attempts"][0]
        assert attempt["dispatch"] == "dispatched" and attempt["cleanup"] == "unknown"
    assert seen == [*expected, ("release", "dispatch-context")]
