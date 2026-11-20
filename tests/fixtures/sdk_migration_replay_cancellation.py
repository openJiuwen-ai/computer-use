# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deterministic SDK and MCP cancellation replay at the native boundary."""

import asyncio
import json
import math
import os
import signal
import sys
from pathlib import Path

SESSION = "strict-mcp-cancel-session"
BINDING = {"session": SESSION, "instance": None, "rootWindow": None, "surface": None, "node": None, "capture": None}
CAPABILITIES = ["cancel", "requestDedup", "nativeOperations", "nativeDirectory"]
UNREACHED = "CANCELLED TOOL CONTINUED"


def line(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"


def write(path, value):
    temp = path.with_suffix(".writing")
    temp.write_text(line(value), encoding="utf-8")
    temp.replace(path)


def backend(out, arm):
    # Match the real driver's opportunity to release active resources after TERM.
    signal.signal(signal.SIGTERM, lambda *_: None)
    calls = [
        ("hello", {"globalInputAllowed": False}),
        ("begin", {"intent": {"kind": "read", "parameters": {}}, "mode": "read", "target": BINDING}),
        ("inspect", {"operation": "catalogue", "target": BINDING, "query": {"kind": "applications"}}),
    ]
    if arm == "sdk-hold":
        calls.append(("cancel", 2))
    if arm == "recover":
        calls.append(("finish", {"operation": "catalogue"}))
    with (out / f"{arm}.wire.jsonl").open("x", buffering=1, encoding="utf-8") as journal:
        consumed = 0
        for raw in sys.stdin:
            request = json.loads(raw)
            journal.write(line(request))
            if consumed >= len(calls):
                write(out / f"{arm}.unexpected", request)
                raise ValueError("Unexpected request after last expected command")
            method, params = calls[consumed]
            if method == "cancel":
                assert request == {"version": 6, "session": SESSION, "cancel": params}, request
                consumed += 1
                continue
            expected = {"version": 6, "id": consumed, "method": method, "params": params}
            if consumed:
                timeout = request["timeoutMs"]
                assert type(timeout) is int and 0 < timeout <= 30000, request
                expected.update(session=SESSION, timeoutMs=timeout)
            assert request == expected, request
            consumed += 1
            if method == "hello":
                result = {
                    "platform": "macos",
                    "globalInputAllowed": False,
                    "maxTimeoutMs": 30000,
                    "capabilities": CAPABILITIES,
                }
            elif method == "begin":
                result = {"operation": "catalogue", "target": BINDING, "remainingMs": 30000}
            elif method == "inspect":
                if arm in ("hold", "sdk-hold"):
                    write(out / "native-hold.json", {"request": request["id"], "activeLease": "catalogue"})
                    continue
                result = {
                    "kind": "applications",
                    "session": SESSION,
                    "platform": "macos",
                    "applications": [],
                    "stopReasons": [],
                    "observed": {"id": "fixed-recovered", "startedAt": 1, "completedAt": 2},
                }
            else:
                result = {"cleanup": "complete"}
            sys.stdout.write(line({"version": 6, "session": SESSION, "id": request["id"], "result": result}))
            sys.stdout.flush()
        write(out / f"{arm}.eof.json", {"consumed": consumed, "expected": len(calls), "activeLease": arm == "hold"})
        assert consumed == len(calls), (consumed, calls)


async def wait_path(path, task=None):
    async with asyncio.timeout(10):
        while not path.exists():
            if task is not None and task.done():
                raise AssertionError(f"MCP request returned before native hold: {task.result()!r}")
            await asyncio.sleep(0.005)
    return json.loads(path.read_text(encoding="utf-8"))


async def exercise(source, out):
    out.mkdir()
    cwd = out / "cwd"
    cwd.mkdir()
    env = {**os.environ, "PYTHONPATH": str(source), "PYTHONHASHSEED": "0", "COMPUTER_USE_LOG_DIR": str(out / "logs")}
    with (out / "server.stderr").open("wb") as errors:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "computer_use_mcp",
            "--no-allow-global-input",
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=errors,
        )

        async def send(message):
            child.stdin.write(line({"jsonrpc": "2.0", **message}).encode())
            await child.stdin.drain()

        async def result(request):
            raw = await asyncio.wait_for(child.stdout.readline(), 12)
            assert raw, f"EOF waiting for MCP request {request}"
            payload = json.loads(raw)
            assert payload["id"] == request, payload
            return payload

        async def tool(request, code):
            await send(
                {"id": request, "method": "tools/call", "params": {"name": "computer_use", "arguments": {"code": code}}}
            )
            return await result(request)

        def setup(arm):
            args = [str(Path(__file__).resolve()), "backend", str(out), arm]
            return (
                "from computer_use.native import connection\n"
                "from pathlib import Path\n"
                f"assert Path(connection.__file__).resolve().is_relative_to(Path({str(source)!r}))\n"
                f"connection.native_driver = lambda: {{'command': {sys.executable!r}, 'args': {args!r}}}"
            )

        try:
            await send(
                {
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "strict-replay", "version": "1"},
                    },
                }
            )
            assert "result" in await result(0)
            await send({"method": "notifications/initialized"})
            held = asyncio.create_task(tool(1, setup("hold") + f"\ncomputer.list_apps()\nprint({UNREACHED!r})"))
            hold = await wait_path(out / "native-hold.json", held)
            assert hold == {"request": 2, "activeLease": "catalogue"} and not held.done(), hold
            await send(
                {"method": "notifications/cancelled", "params": {"requestId": 1, "reason": "fixed cancellation"}}
            )
            cancelled = await held
            assert cancelled == {"jsonrpc": "2.0", "id": 1, "error": {"code": 0, "message": "Request cancelled"}}, (
                cancelled
            )
            eof = await wait_path(out / "hold.eof.json")
            assert eof == {"consumed": 3, "expected": 3, "activeLease": True}, eof
            recovered = await tool(2, setup("recover") + "\nprint('Recovery apps:', computer.list_apps())")
            assert recovered["result"]["content"] == [{"type": "text", "text": "Recovery apps: []"}], recovered
            assert recovered["result"].get("isError") is not True, recovered
        finally:
            child.stdin.close()
            await asyncio.wait_for(child.wait(), 12)
    assert child.returncode == 0, child.returncode
    assert not list(out.glob("*.unexpected"))
    recovery_eof = await wait_path(out / "recover.eof.json")
    assert recovery_eof == {"consumed": 4, "expected": 4, "activeLease": False}, recovery_eof
    mcp_metadata = recovered["result"]["_meta"]["computer-use"]
    journal = Path(mcp_metadata["logPath"]).resolve(strict=True)
    assert journal.parent == (out / "logs").resolve()
    elapsed = mcp_metadata["durationMs"]
    assert type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, elapsed
    mcp_metadata.update(logPath="<tool-journal>", durationMs="<elapsed-ms>")
    events = [json.loads(item) for item in journal.read_text(encoding="utf-8").splitlines()]
    cancellation_reset = [
        event for event in events if event["kind"] == "execution_reset" and event["reason"] == "Call cancelled."
    ]
    assert len(cancellation_reset) == 1 and cancellation_reset[0]["complete"] is True, cancellation_reset
    first_tool = [event for event in events if event["kind"] == "execution_end" and event["executionId"] == 1]
    assert len(first_tool) == 1 and first_tool[0]["isError"] is True, first_tool
    assert UNREACHED not in str(first_tool), first_tool
    assert not any(
        event["kind"] == "command_end" and event.get("executionId") == 1 and event["id"] == 2 for event in events
    )
    write(
        out / "proof.json",
        {
            "cancelled": cancelled,
            "hold": eof,
            "recovered": recovered,
            "recover": recovery_eof,
            "reset": {"reason": "Call cancelled.", "complete": True},
            "cancelled_output": first_tool[0]["content"],
        },
    )


async def exercise_sdk(source, out):
    from computer_use import Computer
    from computer_use.client.execution import ExecutionCancellation, execution_scope
    from computer_use.native import connection

    assert Path(connection.__file__).resolve().is_relative_to(source.resolve()), connection.__file__
    out.mkdir()
    driver_arms = ("sdk-hold", "recover")
    opened_arms = []

    def driver():
        arm = driver_arms[len(opened_arms)]
        opened_arms.append(arm)
        return {"command": sys.executable, "args": [str(Path(__file__).resolve()), "backend", str(out), arm]}

    connection.native_driver = driver
    cancellation = ExecutionCancellation()
    with Computer(allow_global_input=False) as computer:

        def read():
            with execution_scope(computer._executor, cancellation):
                try:
                    return computer.list_apps()
                except asyncio.CancelledError as error:
                    return {"name": type(error).__name__, "notes": getattr(error, "__notes__", [])}

        pending = asyncio.create_task(asyncio.to_thread(read))
        hold = await wait_path(out / "native-hold.json", pending)
        assert hold == {"request": 2, "activeLease": "catalogue"} and not pending.done(), hold
        cancellation.cancel()
        cancelled = await asyncio.wait_for(pending, 10)
        assert cancelled["name"] == "CancelledError", cancelled
        eof = await wait_path(out / "sdk-hold.eof.json")
        assert eof == {"consumed": 4, "expected": 4, "activeLease": False}, eof
        try:
            computer.list_apps()
        except RuntimeError as error:
            retired = {"name": type(error).__name__, "message": str(error)}
        else:
            raise AssertionError("The retired direct SDK instance unexpectedly accepted a new call")
        assert retired == {
            "name": "RuntimeError",
            "message": "Native session is retired; prior admission or cleanup was unconfirmed",
        }, retired
        assert opened_arms == ["sdk-hold"], opened_arms
    with Computer(allow_global_input=False) as fresh_computer:
        recovered = fresh_computer.list_apps()
        assert recovered == [], recovered
        # Closing a POSIX SDK instance can send TERM. The fake driver defers it
        # and lets stdin EOF finish its own resource cleanup.
    recovery_eof = await wait_path(out / "recover.eof.json")
    assert recovery_eof == {"consumed": 4, "expected": 4, "activeLease": False}, recovery_eof
    write(
        out / "proof.json",
        {"cancelled": cancelled, "hold": eof, "retired": retired, "recovered": recovered, "recover": recovery_eof},
    )


if __name__ == "__main__":
    if sys.argv[1] == "backend":
        backend(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "sdk":
        asyncio.run(exercise_sdk(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        asyncio.run(exercise(Path(sys.argv[1]), Path(sys.argv[2])))
