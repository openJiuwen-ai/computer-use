# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""No-UI protocol fixture: the first driver exits after acknowledging read cleanup."""

import json
import os
import sys
from pathlib import Path

spawn_log = Path(sys.argv[1])
first = not spawn_log.exists()
with spawn_log.open("a") as stream:
    stream.write(str(os.getpid()) + "\n")

# Deliberately reuse the wire identity so generation binding is also exercised.
session = "observation-session"
for line in sys.stdin:
    request = json.loads(line)
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
    print(json.dumps(dict(version=6, session=session, id=request["id"], result=result)), flush=True)
    if first and method == "finish":
        sys.exit(0)
