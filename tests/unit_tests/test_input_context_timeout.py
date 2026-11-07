# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A lost input-context query retires the session without implying new input."""

import asyncio
import json
from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
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
            ),
        }
        self.scenario = scenario
        self.sent = []

    async def _connect(self):
        assert self._hello is not None, "an admitted write cannot use a successor session"

    def _write(self, request):
        method, params = request["method"], request["params"]
        self.sent.append(method)
        if method == "begin":
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
