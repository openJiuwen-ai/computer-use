# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unconfirmed native replies preserve the effects possible at each write stage."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.global_input import GlobalInput
from computer_use.interaction.requests import ComputerInput
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import ApplicationBinding, Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort


class SilentStageConnection(Connection):
    """Use the native wire and deadline, withholding a reply at the selected stage."""

    def __init__(self, scenario):
        super().__init__(lambda: None)
        self._hello = {
            "session": "session",
            "platform": "macos",
            "globalInputAllowed": True,
            "maxTimeoutMs": 20,
            "capabilities": (
                "nativeOperations",
                "nativeBoundContracts",
                "nativeUnitReadinessV2",
                "nativeClickPipelineV2",
                "nativeWindowScope",
                "nativePointer",
                "nativeKeyboardIntents",
                "nativeGlobalInput",
                "nativeLaunch",
            ),
        }
        self.scenario = scenario
        self.sent = []

    async def _connect(self):
        assert self._hello is not None, "an admitted write cannot use a successor session"

    def _write(self, request):
        if "cancel" in request:
            self.sent.append(("cancel", request["cancel"]))
            return
        method, params = request["method"], request["params"]
        self.sent.append(method)
        if method == "begin":
            if self.scenario == "lost_begin":
                return
            if self.scenario == "rejected_begin":
                reply = {
                    "version": self.protocol_version,
                    "session": "session",
                    "id": request["id"],
                    "error": {
                        "code": "deadline_exceeded",
                        "target": "native request",
                        "effect": "none",
                        "detail": "native begin deadline exceeded",
                        "next": "read the target",
                    },
                }
                self._receive((json.dumps(reply) + "\n").encode())
                return
            result = {"operation": "write", "target": params["target"], "remainingMs": 20}
            if "clickContract" in params:
                result["clickContract"] = params["clickContract"]
        elif method == "inspect":
            assert params["query"]["kind"] == "inputContext"
            if self.scenario == "before_input" or self.sent.count("inspect") > 1:
                return
            needs_preparation = self.scenario == "after_preparation"
            result = {
                "kind": "inputContext",
                "binding": params["target"],
                "observed": "facts",
                "targetLabel": "Fixture",
                "identity": "yes",
                "withinScope": "yes",
                "enabled": "yes",
                "blockedBy": [],
                "primitives": [
                    {
                        "id": "keyboard",
                        "unit": "key_sequence",
                        "channel": "keyboard",
                        "supported": "yes",
                        "readiness": "no" if needs_preparation else "yes",
                        "pending": ["receiver"] if needs_preparation else [],
                        "backgroundPreparation": "yes",
                    }
                ],
                "input": {
                    "foregroundSurface": None,
                    "keyboardSurface": None,
                    "focusedNode": None,
                    "focusAncestors": [],
                    "editor": None,
                    "editorOwner": None,
                    "selectionKnown": "unknown",
                    "geometryCurrent": "unknown",
                    "windowRoute": "unknown",
                },
            }
        else:
            assert method == "execute"
            if self.scenario == "lost_execute":
                return
            assert self.scenario == "after_preparation" and params["command"]["kind"] == "prepare_receiver"
            result = {
                "target": params["command"]["target"],
                "dispatch": "notDispatched",
                "detail": "Context prepared",
                "preparation": [],
                "confirmation": "confirmed",
                "cleanup": "complete",
                "context": None,
                "errorCode": None,
                "retryReason": None,
                "impact": {
                    "coverage": "complete",
                    "effects": [
                        {
                            "stage": "preparation",
                            "device": "application",
                            "transport": "activation",
                            "mechanism": "NSRunningApplication.activate",
                            "state": "invoked",
                        }
                    ],
                    "foregroundChanged": "yes",
                    "clipboardRestoration": "notUsed",
                    "foregroundRestoration": "unknown",
                },
            }
        reply = {"version": self.protocol_version, "session": "session", "id": request["id"], "result": result}
        self._receive((json.dumps(reply) + "\n").encode())


@pytest.mark.parametrize(
    ("scenario", "expected_effect", "methods"),
    [
        ("before_input", "none", ["begin", "inspect"]),
        ("after_preparation", "possible", ["begin", "inspect", "execute", "inspect"]),
        ("lost_execute", "possible", ["begin", "inspect", "execute"]),
    ],
)
def test_lost_input_context_has_no_effect_of_its_own(monkeypatch, scenario, expected_effect, methods):
    monkeypatch.setattr("computer_use.native.connection.requestCleanupGraceMs", 5)
    connection = SilentStageConnection(scenario)
    port = ConnectionPort(connection, HostPolicy())
    target = Binding("session", "app", "root", "surface", "editor")
    intent = Intent("key", {"key": "F6"})
    if scenario == "before_input":
        target = replace(target, node=None, capture="observed-image")
        intent = Intent("click", {"x": 198, "y": 317, "button": "left", "count": 1})
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(Engine(port).execute(intent, target))
    error = caught.value
    assert connection.sent == methods, str(error)
    assert port.calls.retired and connection.session_identity is None
    assert "no terminal response within 5 ms cleanup grace" in error.detail
    assert any("Native operation cleanup unconfirmed" in note for note in error.__notes__)
    assert error.effect == expected_effect and f"Effect: {expected_effect}" in str(error)
    if scenario == "before_input":
        assert error.code == "execution_failed" and error.action_receipt is None
        assert "read-only request failed" in error.detail and error.impact["attempts"] == ()
    if scenario == "after_preparation":
        (preparation,) = error.impact["attempts"]
        assert preparation["dispatch"] == "notDispatched"
        assert preparation["actual"]["foreground_changed"] == "yes"
        assert preparation["actual"]["foreground_restoration"] == "unknown"
    if scenario == "lost_execute":
        (attempt,) = error.impact["attempts"]
        assert error.code == "execution_unknown" and attempt["dispatch"] == attempt["cleanup"] == "unknown"


@pytest.mark.parametrize("route", ["scoped_key", "launch", "global_key"])
def test_lost_macos_write_begin_reports_unissued_input_and_unknown_scope_cleanup(monkeypatch, route):
    monkeypatch.setattr("computer_use.native.connection.requestCleanupGraceMs", 5)
    connection = SilentStageConnection("lost_begin")
    child = Mock(returncode=None, pid=999, wait=AsyncMock())
    connection._child = child
    port = ConnectionPort(connection, HostPolicy())

    async def run():
        try:
            if route == "global_key":
                await GlobalInput(SimpleNamespace(port=port)).arun(ComputerInput(Intent("key", {"key": "t"}, True)))
            else:
                target = Binding("session", "app", "root", "surface")
                intent = Intent("key", {"key": "t"})
                if route == "launch":
                    target, intent = ApplicationBinding("session", "installation"), Intent("launch")
                await Engine(port).execute(intent, target)
        finally:
            await connection.wait_closed()

    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(run())
    error = caught.value
    assert connection.sent == ["begin", ("cancel", 1)]
    assert port.calls.retired and connection.session_identity is None
    child.stdin.close.assert_called_once_with()
    child.terminate.assert_called_once_with()
    child.wait.assert_awaited_once()
    assert error.code == "execution_failed" and error.effect == "none"
    assert "requested action was not dispatched" in error.detail
    assert error.scope_cleanup == "unknown" and "Scope cleanup: unknown." in str(error)
    assert error.action_receipt is None and error.impact["attempts"] == ()


def test_macos_write_begin_terminal_failure_keeps_confirmed_scope():
    connection = SilentStageConnection("rejected_begin")
    port = ConnectionPort(connection, HostPolicy())
    target = Binding("session", "app", "root", "surface")
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(Engine(port).execute(Intent("key", {"key": "t"}), target))
    assert connection.sent == ["begin"]
    assert not port.calls.retired and connection.session_identity == (0, "session")
    assert caught.value.effect == "none" and caught.value.scope_cleanup is None
    assert caught.value.detail == "native begin deadline exceeded"
