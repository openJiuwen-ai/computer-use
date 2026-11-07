# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dispatch evidence outlives failure to close the native operation scope."""

from dataclasses import replace
from unittest.mock import AsyncMock, call

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import (
    Cleanup,
    ClickContinuation,
    ClickReceipt,
    ClickTransportEvidence,
    Dispatch,
    HostPolicy,
    OpenedOperation,
    StepResult,
)
from unit_tests.mcp.test_popup_delivery import popup_fixture
from unit_tests.test_unified_write_routing import click_route, facts


def finish_fixture(scenario):
    fixture = popup_fixture(capture_parent=True)
    port = fixture.port
    port.policy = HostPolicy()
    fixture.controller.engine = Engine(port)
    directed = click_route("surface_pointer")
    finish_failure = ComputerUseError(
        {"code": "execution_unknown", "effect": "possible", "target": "finish", "detail": "Native finish timed out"}
    )

    original_finish = port.finish

    async def begin(intent, target, timeout):
        port.trace.clear()
        return OpenedOperation("write", target)

    async def inspect(operation, target, timeout):
        return replace(facts(directed), binding=target)

    async def execute(operation, step, command, timeout):
        assert step == 1 and command.target.node is None and command.target.capture is not None
        if scenario in ("lost_execute", "refused"):
            raise ComputerUseError(
                {
                    "code": "input_unavailable" if scenario == "refused" else "execution_unknown",
                    "effect": "none" if scenario == "refused" else "possible",
                    "target": "execute",
                    "detail": "Native execute did not return an input receipt",
                }
            )
        return StepResult(
            command.target,
            Dispatch.UNKNOWN if scenario == "ambiguous" else Dispatch.DISPATCHED,
            "Input delivery was uncertain" if scenario == "ambiguous" else "Pointer sent once",
            error_code="execution_unknown" if scenario == "ambiguous" else None,
            click=ClickReceipt(
                (),
                ClickTransportEvidence(call_entered=True, call_returned=True),
                ClickContinuation("stop", command.target, directed.click.route_family),
            ),
        )

    async def finish(operation):
        if operation != "write":
            return await original_finish(operation)
        if scenario == "finish_unknown":
            return Cleanup.UNKNOWN
        raise finish_failure

    port.begin, port.inspect = begin, inspect
    port.execute, port.finish = AsyncMock(side_effect=execute), AsyncMock(side_effect=finish)
    return fixture, finish_failure


@pytest.mark.parametrize("scenario", ["finish_timeout", "finish_unknown", "lost_execute", "refused", "ambiguous"])
def test_public_click_keeps_only_established_dispatch_on_failed_finish(scenario):
    fixture, finish_failure = finish_fixture(scenario)
    app, port = fixture.app, fixture.port
    assert app.read(view="both").screenshot is not None
    point = app.point((5, 5), window=0)
    with pytest.raises(ComputerUseError) as caught:
        app.click(at=point, window=0)
    error = caught.value
    port.execute.assert_awaited_once()
    assert port.finish.await_args_list.count(call("write")) == 1
    assert port.calls.retired and port.trace == []
    (attempt,) = error.impact["attempts"]

    if scenario in ("finish_timeout", "finish_unknown"):
        assert error.action_receipt.status == "dispatched"
        assert error.action_receipt.detail == "Pointer sent once"
        assert error.action_receipt.intent == "click"
        assert error.action_receipt.target == "Window 0"
        assert error.action_receipt.impact is error.impact
        assert error.scope_cleanup == "unknown"
        assert error.code == "execution_unknown" and error.effect == "possible"
        assert attempt.dispatch == "dispatched" and attempt.cleanup == "complete"
        assert error.observation_status == "unavailable" and error.observation_error.code == "session_unavailable"
        assert not error.observation_error.session_usable
        assert "Action [dispatched]" in str(error) and "Scope cleanup: unknown." in str(error)
        if scenario == "finish_timeout":
            assert error.__cause__ is finish_failure
    else:
        assert getattr(error, "action_receipt", None) is None and getattr(error, "scope_cleanup", None) is None
        assert attempt.dispatch == ("notDispatched" if scenario == "refused" else "unknown")
        assert attempt.cleanup == ("complete" if scenario == "ambiguous" else "unknown")
        assert error.effect == ("none" if scenario == "refused" else "possible")
        assert any("Native finish timed out" in note for note in error.__notes__)
        assert "Action [dispatched]" not in str(error)
