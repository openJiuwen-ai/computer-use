# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A deep accessibility read retains its deferred MCP continuation."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from computer_use_mcp.session import ExecutionSession


def _value(kind, data):
    return {"nativeType": kind, "data": data}


def _tree(depth):
    nodes = []
    for level in range(depth + 1):
        role = "frame" if level == 0 else ("label" if level == depth else "panel")
        children = [_value("AtspiAccessible", {"ref": str(level + 1)})] if level < depth else []
        properties = [
            {"key": ["linux.atspi", "role"], "value": _value("python.str", role)},
            {
                "key": ["linux.atspi", "children"],
                "value": _value("AtspiAccessible[]", {"items": children}),
                "read": {
                    "complete": True,
                    "details": [
                        ["relation", _value("python.bool", True)],
                        ["certainty", _value("python.str", "confirmed")],
                        ["state", _value("python.str", "complete")],
                        ["total", _value("python.int", len(children))],
                    ],
                },
            },
        ]
        if level in (0, depth):
            name = "Fixture window" if level == 0 else "Deep leaf"
            properties.append({"key": ["linux.atspi", "name"], "value": _value("python.str", name)})
        nodes.append(
            {
                "id": str(level),
                "kind": "element",
                "source": "linux.atspi",
                "nativeType": "AtspiAccessible",
                "properties": properties,
            }
        )
    return {
        "kind": "observation",
        "session": "fixture",
        "platform": "linux",
        "roots": ["0"],
        "nodes": nodes,
        "read": {
            "intervalNs": [1, 2],
            "complete": True,
            "details": [
                ["surface", _value("python.str", "surface")],
                ["observation_id", _value("python.str", "tree")],
            ],
        },
    }


def _serve(depth):
    surface = {
        "window": "0",
        "instance": "instance",
        "surface": "surface",
        "node": "0",
        "title": "Fixture window",
        "kind": "window",
        "parent": None,
        "parentState": "none",
        "modal": False,
        "nativeWindow": True,
        "accessibility": True,
    }
    inventory = {
        "session": "fixture",
        "platform": "linux",
        "observed": {"id": "inventory", "startedAt": 1, "completedAt": 2},
        "stopReasons": [],
    }
    directories = {
        "applications": dict(
            inventory,
            kind="applications",
            applications=[
                {
                    "id": "app",
                    "displayName": "Fixture app",
                    "isRunning": True,
                    "path": None,
                    "identifier": None,
                    "matchKeys": [],
                },
            ],
        ),
        "surfaces": dict(
            inventory,
            kind="surfaces",
            application="app",
            surfaces=[surface],
            rootDiscoveryComplete=True,
            provider={"status": "atspi", "attribute": None, "writeStatus": None},
        ),
        "nodes": _tree(depth),
    }
    for line in sys.stdin:
        request = json.loads(line)
        params = request["params"]
        match request["method"]:
            case "hello":
                result = {
                    "platform": "linux",
                    "globalInputAllowed": False,
                    "maxTimeoutMs": 30000,
                    "capabilities": [
                        "cancel",
                        "requestDedup",
                        "nativeOperations",
                        "nativeDirectory",
                        "nativeObservationV1",
                        "nativeRelations",
                        "nativeNodeDepthBudget",
                        "nativeWindowScope",
                    ],
                }
            case "begin":
                result = {"operation": str(request["id"]), "target": params["target"], "remainingMs": 30000}
            case "inspect":
                result = directories[params["query"]["kind"]]
            case "finish":
                result = {"cleanup": "complete"}
            case unexpected:
                raise AssertionError(f"Unexpected driver method {unexpected}")
        sys.stdout.write(json.dumps({"version": 6, "session": "fixture", "id": request["id"], "result": result}) + "\n")
        sys.stdout.flush()


@pytest.mark.parametrize("depth", [8, 240])
@pytest.mark.usefixtures("native_read")
def test_deep_read_retains_mcp_continuation(depth, monkeypatch, tmp_path):
    monkeypatch.setenv("COMPUTER_USE_LOG_DIR", str(tmp_path))

    async def run():
        session = ExecutionSession(allow_global_input=False)
        try:

            async def execute(code):
                result = await session.execute(code)
                text = "\n".join(block["text"] for block in result["content"])
                assert not result.get("isError"), text
                return text

            await execute(
                "import sys\n"
                "from computer_use.native import connection\n"
                "from computer_use_mcp.output import OutputBuffer\n"
                f"connection.native_driver = lambda: {{'command': sys.executable, "
                f"'args': [{str(Path(__file__).resolve())!r}, {str(depth)!r}]}}\n"
                "sys.setrecursionlimit(256)\n"
                # A small synthetic tree with minimal fields can exhaust this smaller text budget.
                "OutputBuffer.TEXT_LIMIT = 65536\n"
                "app = computer.app(computer.list_apps()[0].id)"
            )
            delivered = await execute("observation = app.read(view='tree', full=True)")
            if depth == 8:
                assert "Tree: full" in delivered
                assert "Deep leaf" in delivered
                return
            assert "Tree: deferred (MCP text budget)." in delivered
            assert "captured_observation_1.read_details()" in delivered
            await execute("assert 'Presentation fallback' in observation.text and 'RecursionError' in observation.text")
            excerpt = await execute("page = captured_observation_1.read_details(length=64)\nprint(page.text)")
            assert excerpt.startswith("Captured details (same observation)")
            await execute(
                "assert page.next is not None\n"
                "following = captured_observation_1.read_details(cursor=page.next, length=64)\n"
                "assert len(following.text) == 64\n"
                "assert page.text + following.text == captured_observation_1.read_details(length=128).text"
            )
        finally:
            await session.close()

    asyncio.run(run())


if __name__ == "__main__":
    _serve(int(sys.argv[1]))
