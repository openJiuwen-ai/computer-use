# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Receive metrics separate decoding without changing response completion."""

import asyncio
import json
from unittest.mock import Mock, patch

import pytest
from computer_use.errors import ComputerUseError
from computer_use.native import connection as native_connection


def pending_connection():
    invalidated = Mock()
    connection = native_connection.Connection(invalidated)
    connection._hello = {"session": "session"}
    future = asyncio.get_running_loop().create_future()
    timer = Mock()
    connection._pending = {
        "id": 1,
        "method": "finish",
        "traceId": "trace",
        "startedAt": 90.0,
        "target": None,
        "future": future,
        "timer": timer,
        "pure": True,
        "writeAdmission": False,
        "cancelSent": False,
    }
    return connection, future, timer, invalidated


def frame(*, backend_error=False, malformed=False):
    response = {"version": 6, "session": "session", "id": 1}
    if backend_error:
        response["error"] = {
            "code": "execution_failed",
            "target": "finish",
            "effect": "none",
            "detail": "记录的失败",
            "next": "inspect the result",
        }
    else:
        response["result"] = {"cleanup": "invalid" if malformed else "complete"}
    return json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("backend_error", [False, True])
def test_receive_reports_decode_costs_and_complete_frame_bytes(backend_error):
    async def run():
        connection, future, timer, invalidated = pending_connection()
        events = []
        clock = [100.0]
        original_json = native_connection.json.loads
        decode_name = "decode_failure" if backend_error else "decode_execution_result"
        original_protocol = getattr(native_connection, decode_name)
        decode_calls = []

        def decode_json(*args, **kwargs):
            clock[0] += 2.0
            return original_json(*args, **kwargs)

        def decode_protocol(*args, **kwargs):
            clock[0] += 3.0
            decode_calls.append((args, kwargs))
            return original_protocol(*args, **kwargs)

        def diagnostic(event):
            events.append(event)
            if event["kind"] == "command_end":
                clock[0] += 4.0

        encoded = frame(backend_error=backend_error)
        with (
            patch.object(native_connection, "_now", lambda: clock[0]),
            patch.object(native_connection.json, "loads", decode_json),
            patch.object(native_connection, decode_name, decode_protocol),
            patch("computer_use.diagnostics._handler", diagnostic),
        ):
            connection._receive(encoded[:17])
            assert not future.done() and events == []
            connection._receive(encoded[17:] + b"\r\n")

        if backend_error:
            with pytest.raises(ComputerUseError) as caught:
                await future
            assert caught.value.code == "execution_failed"
            assert caught.value.detail == "记录的失败"
        else:
            assert await future == {"cleanup": "complete"}
            assert decode_calls[0][1] == {"retain_observation": True}
        assert len(decode_calls) == 1
        timer.cancel.assert_called_once_with()
        invalidated.assert_not_called()
        assert connection._pending is None
        assert [event["kind"] for event in events] == ["command_end", "command_diagnostic_end"]
        event = events[-1]
        assert event["traceId"] == "trace" and event["id"] == 1 and event["method"] == "finish"
        assert event["jsonDecodeMs"] == 2.0
        assert event["protocolDecodeMs"] == 3.0
        assert event["frameBytes"] == len(encoded)
        assert event["diagnosticMs"] == 4.0

    asyncio.run(run())


@pytest.mark.parametrize("cancelled", [False, True])
def test_diagnostic_failure_does_not_change_completion_or_cancelled_future(cancelled):
    async def run():
        connection, future, timer, invalidated = pending_connection()
        events = []

        def failing_handler(event):
            events.append(event)
            raise RuntimeError("diagnostic sink failed")

        if cancelled:
            future.cancel()
        with patch("computer_use.diagnostics._handler", failing_handler):
            connection._receive(frame() + b"\n")
        if cancelled:
            assert future.cancelled()
        else:
            assert await future == {"cleanup": "complete"}
        timer.cancel.assert_called_once_with()
        invalidated.assert_not_called()
        assert connection._pending is None
        assert [event["kind"] for event in events] == ["command_end", "command_diagnostic_end"]

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [b'{"broken":', frame(malformed=True)])
def test_invalid_json_or_protocol_keeps_original_connection_failure(invalid):
    async def run():
        connection, future, timer, invalidated = pending_connection()
        events = []
        with patch("computer_use.diagnostics._handler", events.append):
            connection._receive(invalid + b"\n")
        with pytest.raises(ComputerUseError) as caught:
            await future
        assert caught.value.code == "execution_failed" and caught.value.effect == "none"
        timer.cancel.assert_called_once_with()
        invalidated.assert_called_once_with()
        assert connection._pending is None and connection._hello is None
        assert [event["kind"] for event in events] == ["command_end"]
        assert events[0]["stage"] == "connection"

    asyncio.run(run())
