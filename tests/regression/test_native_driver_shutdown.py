# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process shutdown must reach a driver that cannot read its stdin."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _driver(directory):
    exit_reason = "deadline"

    def terminate(_signum, _frame):
        nonlocal exit_reason
        exit_reason = "SIGTERM"
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    hello = json.loads(sys.stdin.readline())
    response = {
        "version": 6,
        "session": "shutdown-test",
        "id": hello["id"],
        "result": {
            "platform": "macos",
            "globalInputAllowed": False,
            "maxTimeoutMs": 30000,
            "capabilities": ["cancel", "requestDedup", "nativeOperations", "nativeDirectory"],
        },
    }
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
    assert json.loads(sys.stdin.readline())["method"] == "inspect"
    try:
        (directory / "blocked").touch()
        time.sleep(5)
    finally:
        marker = directory / "exit.pending"
        marker.write_text(exit_reason, encoding="utf-8")
        marker.replace(directory / "exit")


def _host(directory):
    from computer_use.native.connection import Connection

    async def wait_until_blocked():
        async with asyncio.timeout(3):
            while not (directory / "blocked").exists():
                await asyncio.sleep(0.005)

    connection = Connection(
        lambda: None,
        driver={"command": sys.executable, "args": [__file__, "driver", str(directory)]},
        allow_global_input=False,
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(connection.connect(3000))
    held = loop.create_task(connection.request("inspect", {"operation": "test", "query": {"kind": "applications"}}))
    loop.run_until_complete(wait_until_blocked())
    assert not held.done()
    return loop, connection, held


@pytest.mark.skipif(os.name != "posix", reason="Windows driver shutdown uses EOF")
def test_interpreter_shutdown_signals_a_blocked_native_driver(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPUTER_USE_DEMO_FOREGROUND", raising=False)
    subprocess.run([sys.executable, __file__, "host", str(tmp_path)], check=True, capture_output=True, timeout=8)
    marker = tmp_path / "exit"
    deadline = time.monotonic() + 6
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert marker.read_text(encoding="utf-8") == "SIGTERM"


if __name__ == "__main__":
    role, root = sys.argv[1:]
    if role == "driver":
        _driver(Path(root))
    else:
        # Keep the live connection and request through interpreter shutdown.
        held_session = _host(Path(root))
