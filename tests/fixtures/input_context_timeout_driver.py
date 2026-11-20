# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local protocol-v6 child that deliberately withholds selected replies."""

import json
import os
import signal
import sys
from pathlib import Path

SESSION = "session"
MAX_TIMEOUT_MS = 200
CAPABILITIES = [
    "cancel",
    "requestDedup",
    "desktopCoordination",
    "nativeOperations",
    "nativeBoundContracts",
    "nativeUnitReadinessV2",
    "nativeClickPipelineV2",
    "nativeWindowScope",
    "nativePointer",
    "nativeKeyboardIntents",
    "nativeGlobalInput",
    "nativeLaunch",
]


def response(request, scenario, seen_inspects):
    method, params = request["method"], request["params"]
    envelope = {"version": 6, "session": SESSION, "id": request["id"]}
    if method == "hello":
        result = {
            "platform": "macos",
            "globalInputAllowed": params["globalInputAllowed"],
            "maxTimeoutMs": MAX_TIMEOUT_MS,
            "capabilities": CAPABILITIES,
        }
    elif method == "begin":
        if scenario == "lost_begin":
            return None
        if scenario == "rejected_begin":
            return {
                **envelope,
                "error": {
                    "code": "deadline_exceeded",
                    "target": "native request",
                    "effect": "none",
                    "detail": "native begin deadline exceeded",
                    "next": "read the target",
                },
            }
        result = {"operation": "write", "target": params["target"], "remainingMs": MAX_TIMEOUT_MS}
        if "clickContract" in params:
            result["clickContract"] = params["clickContract"]
    elif method == "inspect":
        assert params["query"]["kind"] == "inputContext"
        if scenario == "before_input" or seen_inspects > 1:
            return None
        needs_preparation = scenario == "after_preparation"
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
        if scenario == "lost_execute":
            return None
        assert scenario == "after_preparation" and params["command"]["kind"] == "prepare_receiver"
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
    return {**envelope, "result": result}


def run(scenario, journal):
    def record(value):
        with journal.open("a", encoding="utf-8") as log:
            log.write(json.dumps(value) + "\n")

    def retired(*_args):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        record({"retired": os.getpid()})
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, retired)
    seen_inspects = 0
    for line in sys.stdin:
        request = json.loads(line)
        record(request)
        if "cancel" in request:
            continue
        seen_inspects += int(request["method"] == "inspect")
        reply = response(request, scenario, seen_inspects)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    retired()


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]))
