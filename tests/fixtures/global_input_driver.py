# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A protocol-6 peer exercising global input without touching the desktop."""

import json
import sys


def run(journal, configuration):
    platform, dispatch, cleanup, confirmation, global_input = json.loads(configuration)
    capabilities = ["cancel", "requestDedup", "nativeOperations"]
    if global_input:
        capabilities.extend(("nativeGlobalInput", "nativePointLocation"))
    for line in sys.stdin:
        request = json.loads(line)
        method, params = request["method"], request["params"]
        if method != "hello":
            with open(journal, "a", encoding="utf-8") as stream:
                stream.write(json.dumps([method, params]) + "\n")
        if method == "hello":
            result = {
                "platform": platform,
                "globalInputAllowed": params["globalInputAllowed"],
                "maxTimeoutMs": 30000,
                "capabilities": capabilities,
            }
        elif method == "begin":
            result = {"operation": "operation", "target": params["target"], "remainingMs": 30000}
        elif method == "execute":
            result = {
                "target": params["command"]["target"],
                "dispatch": dispatch,
                "detail": "Input delivered",
                "preparation": [],
                "confirmation": confirmation,
                "cleanup": "complete",
                "context": None,
                "errorCode": None if dispatch == "dispatched" or confirmation == "confirmed" else "execution_unknown",
                "retryReason": None,
            }
        elif method == "finish":
            result = {"cleanup": cleanup}
        elif method == "locatePoint":
            point = params["point"]
            result = {"point": {"x": -90, "y": 24} if point is None else {"x": point["x"] - 300, "y": point["y"] + 40}}
        else:
            raise AssertionError(f"Raw input unexpectedly requested {method}")
        envelope = {"version": 6, "session": "session", "id": request["id"], "result": result}
        sys.stdout.write(json.dumps(envelope) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run(*sys.argv[1:])
