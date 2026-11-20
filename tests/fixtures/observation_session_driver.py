# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""No-UI protocol fixture for read timeouts and session exit after cleanup."""

import json
import os
import sys
from pathlib import Path

spawn_log = Path(sys.argv[1])
first = not spawn_log.exists()
timeout_reply = sys.argv[2] if len(sys.argv) > 2 else None
with spawn_log.open("a") as stream:
    stream.write(str(os.getpid()) + "\n")

# Deliberately reuse the wire identity so generation binding is also exercised.
session = "observation-session"
held = None
for line in sys.stdin:
    request = json.loads(line)
    if "cancel" in request:
        assert held == request["cancel"]
        if timeout_reply == "acknowledged":
            error = {
                "code": "deadline_exceeded",
                "target": "inspect",
                "effect": "none",
                "detail": "request deadline exceeded",
                "next": "read again",
            }
            sys.stdout.write(json.dumps({"version": 6, "session": session, "id": held, "error": error}) + "\n")
            sys.stdout.flush()
        continue
    method = request["method"]
    if method == "hello":
        result = dict(
            platform="macos",
            globalInputAllowed=request["params"]["globalInputAllowed"],
            maxTimeoutMs=30000,
            capabilities=["cancel", "requestDedup", "nativeOperations", "nativeDirectory"],
        )
    elif method == "begin":
        assert request["params"]["mode"] == "read"
        result = dict(operation="read", target=request["params"]["target"], remainingMs=30000)
    elif method == "inspect":
        query = request["params"]["query"]
        result = dict(
            kind=query["kind"],
            session=session,
            platform="macos",
            observed=dict(id="observed", startedAt=1, completedAt=2),
            stopReasons=[],
        )
        if query["kind"] == "surfaces":
            if timeout_reply is not None and held is None:
                held = request["id"]
                continue
            result.update(
                application=query["application"],
                surfaces=[],
                rootDiscoveryComplete=True,
                provider=dict(status="available", attribute=None, writeStatus=None),
            )
        else:
            assert query["kind"] == "applications"
            result["applications"] = []
    elif method == "finish":
        result = dict(cleanup="complete")
    else:
        raise AssertionError("Unexpected native method: " + method)
    sys.stdout.write(json.dumps(dict(version=6, session=session, id=request["id"], result=result)) + "\n")
    sys.stdout.flush()
    if first and timeout_reply is None and method == "finish":
        sys.exit(0)
