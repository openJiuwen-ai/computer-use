# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synthetic protocol-v6 driver for SDK migration differential replay."""

import json
import signal
import sys
from pathlib import Path

SESSION = "00000000-0000-0000-0000-000000000001"
APPLICATION = {
    "id": "running:fixture",
    "displayName": "Stage Zero Fixture",
    "isRunning": True,
    "path": "/Applications/StageZeroFixture.app",
    "identifier": "local.fixture.stagezero",
}
SURFACE = {
    "window": "window:fixture",
    "instance": APPLICATION["id"],
    "surface": "surface:fixture",
    "node": "window-node",
    "title": "Stage Zero Window",
    "kind": "window",
    "parent": None,
    "parentState": "none",
    "modal": False,
    "nativeWindow": True,
    "accessibility": True,
}
CAPABILITIES = [
    "cancel",
    "requestDedup",
    "desktopCoordination",
    "nativeOperations",
    "nativeDirectory",
    "nativeBoundContracts",
    "nativeUnitReadinessV2",
    "nativeActionIntents",
    "nativeApplicationListing",
]
ACTION = "AXPress"


def scalar(value):
    return {"nativeType": "CFString", "data": value}


def array(items):
    return {"nativeType": "CFArray", "data": {"items": items}}


def field(name, value, namespace="macos.ax"):
    return {"key": [namespace, name], "value": value}


def complete(field, **evidence):
    values = {"relation": "CFBoolean", "total": "CFNumber", "certainty": "CFString", "state": "CFString"}
    return {
        **field,
        "read": {
            "complete": True,
            "details": [[key, {"nativeType": values[key], "data": value}] for key, value in evidence.items()],
        },
    }


def children(*nodes):
    return complete(
        field("AXChildren", array([{"nativeType": "AXUIElement", "data": {"ref": node}} for node in nodes])),
        relation=True,
        certainty="confirmed",
        state="complete",
        total=len(nodes),
    )


def record(node, role, title, *fields):
    return {
        "id": node,
        "source": "macos.ax",
        "kind": "element",
        "nativeType": "AXUIElement",
        "properties": [field("AXRole", scalar(role)), field("AXTitle", scalar(title)), *fields],
    }


def capture(target, query, tick, writes):
    action = {"nativeType": "record", "data": {"fields": [["name", scalar(ACTION)]]}}
    title = f"Run fixture action — write {writes}, capture {tick}"
    rows = [
        record(
            "window-node",
            "AXWindow",
            "Stage Zero Window",
            children("button-node"),
            complete(field("actions", array([]), "toolkit"), state="complete", total=0),
        ),
        record(
            "button-node",
            "AXButton",
            title,
            children(),
            complete(field("actions", array([action]), "toolkit"), state="complete", total=1),
        ),
    ]
    return {
        "kind": "observation",
        "session": SESSION,
        "platform": "macos",
        "roots": [target["node"]],
        "nodes": rows if target["node"] == "window-node" else rows[1:],
        "read": {
            "intervalNs": [tick, tick + 1],
            "details": [
                ["observation_id", scalar(f"capture:{tick}")],
                ["surface", scalar(target["surface"])],
                ["requested_relations", array([scalar(item) for item in query.get("followRelations", [])])],
            ],
        },
    }


def input_context(target, tick):
    return {
        "kind": "inputContext",
        "binding": target,
        "observed": f"input:{tick}",
        "targetLabel": "Run fixture action",
        "identity": "yes",
        "withinScope": "yes",
        "enabled": "yes",
        "blockedBy": [],
        "nativeRole": "AXButton",
        "actionsComplete": True,
        "primitives": [
            {
                "id": "primitive:press",
                "unit": "native_action",
                "channel": "object",
                "supported": "yes",
                "readiness": "yes",
                "pending": [],
                "backgroundPreparation": "no",
                "action": {"name": ACTION},
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


def dispatch(target):
    return {
        "target": target,
        "dispatch": "dispatched",
        "detail": "Fixture action dispatched once.",
        "preparation": [],
        "confirmation": "notRequested",
        "cleanup": "complete",
        "errorCode": None,
        "retryReason": None,
        "context": None,
    }


def reply(request, writes):
    method = request["method"]
    params = request["params"]
    index = request["id"]
    tick = 10 * (index + 1)
    if method == "hello":
        assert params == {"globalInputAllowed": False}, params
        return {"platform": "macos", "globalInputAllowed": False, "maxTimeoutMs": 120000, "capabilities": CAPABILITIES}
    if method == "begin":
        return {"operation": f"operation:{index}", "target": params["target"], "remainingMs": 120000}
    if method == "finish":
        return {"cleanup": "complete"}
    if method == "execute":
        return dispatch(params["command"]["target"])
    assert method == "inspect", request
    query = params["query"]
    kind = query["kind"]
    common = {
        "kind": kind,
        "session": SESSION,
        "platform": "macos",
        "stopReasons": [],
        "observed": {"id": f"directory:{tick}", "startedAt": tick, "completedAt": tick + 1},
    }
    if kind == "applications":
        if query.get("includeEnded"):
            common["endedApplications"] = []
        return {**common, "applications": [APPLICATION]}
    if kind == "surfaces":
        return {
            **common,
            "application": APPLICATION["id"],
            "surfaces": [SURFACE],
            "focusedWindow": SURFACE["window"],
            "rootDiscoveryComplete": True,
            "provider": {"status": "available", "attribute": None, "writeStatus": None},
        }
    if kind == "nodes":
        return capture(params["target"], query, tick, writes)
    if kind == "inputContext":
        return input_context(params["target"], tick)
    raise AssertionError(request)


def run(ledger, tape, hold=None):
    requests = json.loads(tape.read_text(encoding="utf-8")) if tape is not None else None
    expected = iter(requests) if requests is not None else None
    held_id = requests[-1]["cancel"] if hold is not None else None
    if hold is not None:
        # Allow the blocked read to consume cancellation before stdin closes.
        signal.signal(signal.SIGTERM, lambda *_: None)
    writes = 0
    with (
        ledger.open("x", encoding="utf-8", buffering=1) as stream,
        ledger.with_suffix(".raw.jsonl").open("xb") as raw_stream,
    ):
        for line in sys.stdin.buffer:
            raw_stream.write(line)
            raw_stream.flush()
            request = json.loads(line)
            timeout = request.pop("timeoutMs", None)
            if request.get("method") != "hello" and "cancel" not in request:
                assert type(timeout) is int and 0 < timeout <= 120000, request
            stream.write(json.dumps(request) + "\n")
            if expected is not None and json.dumps(request, sort_keys=True) != json.dumps(
                next(expected, None), sort_keys=True
            ):
                ledger.with_suffix(".mismatch").write_text(json.dumps(request), encoding="utf-8")
                raise AssertionError(f"Unexpected native request {request!r}")
            if "cancel" in request:
                continue
            if request["id"] == held_id:
                hold.touch()
                continue
            if request["method"] == "execute":
                writes += 1
            result = reply(request, writes)
            response = {"version": 6, "session": SESSION, "id": request["id"], "result": result}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run(
        Path(sys.argv[1]),
        Path(sys.argv[2]) if len(sys.argv) > 2 else None,
        Path(sys.argv[3]) if len(sys.argv) > 3 else None,
    )
