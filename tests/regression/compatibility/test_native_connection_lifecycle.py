# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native driver startup and cancellation preserve the requesting context."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from computer_use.errors import ComputerUseError

SESSION = "00000000-0000-0000-0000-000000000001"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS startup child and diagnostic pipe")
@pytest.mark.parametrize("override", [False, True])
def test_invalid_driver_exec_reports_the_child_hello_failure(tmp_path, monkeypatch, override):
    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.native import connection as native

    driver = tmp_path / "driver"
    driver.write_bytes(b"not an executable format\n")
    driver.chmod(0o700)
    spec = {"command": str(driver), "args": []}
    monkeypatch.setattr(native, "native_driver", lambda: spec)
    monkeypatch.setattr(native, "startup_provenance", lambda _command: {})

    async def check():
        owner = asyncio.current_task()
        events, invalidations = [], []
        connection = native.Connection(
            lambda: invalidations.append((asyncio.current_task(), sys.exception(), connection.session_identity)),
            spec if override else None,
        )

        exited_in_callback = False

        def diagnostic(event):
            nonlocal exited_in_callback
            events.append(event)
            if event["kind"] == "connection":
                # A closed child input pipe must not hide its terminal stderr.
                until = time.monotonic() + 3
                while time.monotonic() < until:
                    state = subprocess.run(
                        ["/bin/ps", "-o", "stat=", "-p", str(event["pid"])],
                        capture_output=True, text=True, check=False,
                    )
                    if state.returncode == 1 or state.stdout.lstrip().startswith("Z"):
                        exited_in_callback = True
                        return
                    time.sleep(0.01)

        set_diagnostic_handler(diagnostic)
        try:
            with pytest.raises(ComputerUseError) as failure:
                await connection.request("finish", {"operation": "startup-fixture"}, {"timeoutMs": 5000})
            await connection.wait_closed()
        finally:
            set_diagnostic_handler(None)
        error = failure.value
        assert (error.code, error.target, error.effect) == ("execution_failed", "hello", "none")
        assert isinstance(error.__cause__, RuntimeError)
        assert "driver disconnected:" in str(error.__cause__)
        assert "Exec format error" in error.detail and str(driver) in error.detail
        assert len(invalidations) == 2
        assert invalidations[0][0] is not owner and invalidations[0][1:] == (None, None)
        assert invalidations[1] == (owner, error, None)
        assert exited_in_callback
        startup, *stderr = events
        assert startup["kind"] == "connection" and startup["executable"] == str(driver)
        assert all(event["kind"] == "backend_diagnostic" and event["pid"] == startup["pid"] for event in stderr)
        assert stderr[-1]["value"].endswith(f"Exec format error: {str(driver)!r}")
        with pytest.raises(ProcessLookupError):
            os.kill(startup["pid"], 0)

    asyncio.run(check())

def test_missing_installed_driver_reports_one_prelaunch_failure(tmp_path, monkeypatch):
    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.native import connection as native

    monkeypatch.setattr(native, "native_driver", lambda: {"command": str(tmp_path / "missing")})

    async def check():
        events, invalidations = [], []
        connection = native.Connection(lambda: invalidations.append(sys.exception()))
        set_diagnostic_handler(events.append)
        try:
            with pytest.raises(ComputerUseError) as failure:
                await connection.connect()
            await connection.wait_closed()
        finally:
            set_diagnostic_handler(None)
        assert invalidations == [failure.value]
        assert (failure.value.code, failure.value.target, failure.value.effect) == (
            "execution_failed", "connection", "none"
        )
        assert [event["kind"] for event in events] == ["local_error"]

    asyncio.run(check())

def test_cancelled_request_closes_in_the_active_exception_context(tmp_path, monkeypatch):
    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.native.connection import Connection

    monkeypatch.delenv("COMPUTER_USE_DEMO_FOREGROUND", raising=False)
    params = {"operation": "held-finish"}
    tape = [
        {"version": 6, "id": 0, "method": "hello", "params": {"globalInputAllowed": False}},
        {"version": 6, "session": SESSION, "id": 1, "method": "finish", "params": params},
    ]
    ledger, hold = tmp_path / "wire.jsonl", tmp_path / "held"

    script = r"""
import json, sys
from pathlib import Path

ledger, hold, session = sys.argv[1:]
with Path(ledger).open("w", buffering=1) as output:
    hello = json.loads(sys.stdin.readline())
    print(json.dumps(dict(version=6, session=session, id=hello["id"], result=dict(
        platform="macos", globalInputAllowed=False, maxTimeoutMs=30000,
        capabilities=["cancel", "requestDedup", "nativeOperations"],
    ))), flush=True)
    output.write(json.dumps(hello) + "\n")
    command = json.loads(sys.stdin.readline())
    command.pop("timeoutMs")
    output.write(json.dumps(command) + "\n")
    Path(hold).touch()
    sys.stdin.readline()
"""

    async def check():
        observed = []

        def record(kind):
            observed.append((kind, asyncio.current_task(), sys.exception(), connection.session_identity))

        def on_event(event):
            if event["kind"] == "command_end":
                record("command_end")

        connection = Connection(
            lambda: record("invalidate"),
            {"command": sys.executable, "args": ["-u", "-c", script, str(ledger), str(hold), SESSION]},
            allow_global_input=False,
        )
        set_diagnostic_handler(on_event)
        try:
            task = asyncio.create_task(connection.request("finish", params, {"timeoutMs": 5000}))
            async with asyncio.timeout(3):
                while not hold.exists():
                    await asyncio.sleep(0.002)
            # Shutdown can terminate the child before it reads the cancel frame.
            # Observe the successful pipe write while keeping actual I/O in place.
            pipe = connection._child.stdin
            write = pipe.write

            def observe_write(data):
                write(data)
                observed.append(("cancel_write", asyncio.current_task(), sys.exception(), json.loads(data)))

            monkeypatch.setattr(pipe, "write", observe_write)
            task.cancel("held request cancelled")
            with pytest.raises(asyncio.CancelledError, match="held request cancelled") as cancelled:
                await task
            await connection.wait_closed()
        finally:
            set_diagnostic_handler(None)

        assert observed == [
            ("cancel_write", task, cancelled.value, {"version": 6, "session": SESSION, "cancel": 1}),
            ("command_end", task, cancelled.value, None),
            ("invalidate", task, cancelled.value, None),
        ]
        assert [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] == tape

    asyncio.run(check())


@pytest.mark.skipif(os.name != "posix", reason="Driver termination and preview pipe use POSIX")
@pytest.mark.parametrize("replay", ["lifecycle", "idle"])
def test_native_connection_retires_without_reentering_or_losing_idle_events(tmp_path, replay):
    source = Path(__file__).resolve().parents[3]
    fixture = source / "tests" / "fixtures" / f"sdk_transport_{replay}_replay.py"
    completed = subprocess.run(
        [sys.executable, str(fixture), str(source), str(tmp_path / "replay")],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(source), "COMPUTER_USE_DEMO_FOREGROUND": "0"},
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(os.name != "posix", reason="Signal and pipe behavior exercised on POSIX")
@pytest.mark.parametrize("trigger", ["backend_diagnostic", "request_cancelling"])
def test_connection_explicit_close_invalidates_synchronously_and_reports_uncertain_effect(
    tmp_path, monkeypatch, trigger
):
    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.native.connection import Connection

    monkeypatch.delenv("COMPUTER_USE_DEMO_FOREGROUND", raising=False)
    ledger = tmp_path / "explicit-close-wire"
    script = """
import json, pathlib, signal, sys

session, ledger = sys.argv[1], pathlib.Path(sys.argv[2])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
hello = json.loads(sys.stdin.readline())
result = dict(platform="macos", globalInputAllowed=True, maxTimeoutMs=120000,
              capabilities=["cancel", "requestDedup", "nativeOperations"])
print(json.dumps(dict(version=6, session=session, id=hello["id"], result=result)), flush=True)
with ledger.open("w") as output:
    command = json.loads(sys.stdin.readline())
    output.write(json.dumps(command) + "\\n")
    output.flush()
    print(json.dumps({"requestId": command["id"], "message": "accepted"}), file=sys.stderr, flush=True)
    output.write(sys.stdin.readline())
"""
    session = Connection(lambda: None,
        {"command": sys.executable, "args": ["-u", "-c", script, SESSION, str(ledger)]}, allow_global_input=True,
    )
    observed = []
    reason = RuntimeError("closed by host")

    def event(value):
        observed.append(value)
        if value["kind"] == trigger:
            assert session.session_identity is not None
            session.close(reason)
            assert session.session_identity is None

    async def scenario():
        command = {
            "kind": "global_input",
            "target": {"session": SESSION, "global": True},
            "primitiveId": "global",
            "foreground": False,
        }
        set_diagnostic_handler(event)
        try:
            with pytest.raises(ComputerUseError) as closed:
                await session.request(
                    "execute", {"operation": "fixture", "step": 1, "command": command},
                    {"timeoutMs": 500 if trigger == "request_cancelling" else 3000},
                )
        finally:
            set_diagnostic_handler(None)
        assert (closed.value.code, closed.value.effect) == ("execution_unknown", "possible")
        assert closed.value.detail.startswith("closed by host;")
        assert closed.value.__cause__ is reason
        await session.wait_closed()

    asyncio.run(scenario())
    wire = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(wire) == 2
    assert wire[0]["method"] == "execute"
    assert wire[1] == {"version": 6, "session": SESSION, "cancel": 1}
    assert [value["status"] for value in observed if value["kind"] == "command_end"] == ["error"]


@pytest.mark.parametrize(
    ("platform", "override", "installed", "error"),
    [
        ("darwin", False, True, None),
        ("win32", False, True, None),
        ("fixture-os", True, False, None),
        ("fixture-os", False, False, "unsupported_operation"),
        ("linux", False, False, "execution_failed"),
    ],
)
def test_native_launch_preserves_driver_override_and_platform_transport(
    tmp_path, monkeypatch, platform, override, installed, error
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from computer_use.native import connection as native

    binary = tmp_path / "driver"
    if installed:
        binary.touch()
    spec = {"command": str(binary), "args": ["fixture-argument"]}
    discover = Mock(return_value=spec)
    process = SimpleNamespace(pid=42)
    spawn = AsyncMock(return_value=process)
    provenance = {"source": "fixture"}
    preview = Mock(return_value=None)
    tracked = Mock()
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform=platform, executable="fixture-python"))
    monkeypatch.setattr(native, "native_driver", discover)
    monkeypatch.setattr(native, "installed_backend_unavailable_reason", lambda: None)
    monkeypatch.setattr(native, "startup_provenance", lambda _command: provenance)
    monkeypatch.setattr(native, "get_preview_handler", preview)
    monkeypatch.setattr(native.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("COMPUTER_USE_PREVIEW_FD", "3")
    monkeypatch.setenv("COMPUTER_USE_LIFETIME_FD", "4")

    session = native.Connection(lambda: None, spec if override else None)
    monkeypatch.setattr(session, "_track_child", tracked)
    if error is not None:
        with pytest.raises(ComputerUseError) as failure:
            asyncio.run(session._connect())
        assert (failure.value.code, failure.value.target) == (error, "connection")
        spawn.assert_not_awaited()
        tracked.assert_not_called()
    else:
        asyncio.run(session._connect())
        spawn.assert_awaited_once()
        tracked.assert_called_once_with(process, str(binary), provenance)
        arguments, options = spawn.await_args
        environment = options["env"]
        assert arguments[-2:] == (str(binary), "fixture-argument")
        if platform == "win32":
            preview.assert_not_called()
            assert arguments == (str(binary), "fixture-argument")
            assert options["creationflags"] == native.subprocess.CREATE_NO_WINDOW
            assert "COMPUTER_USE_PREVIEW_FD" not in environment and "COMPUTER_USE_LIFETIME_FD" not in environment
        else:
            preview.assert_called_once_with()
            assert arguments[:2] == ("fixture-python", native.driver_exec.__file__)
            assert options["pass_fds"] == (4,)
    assert discover.call_count == int(not override and platform != "fixture-os")


def test_native_driver_preserves_mapping_values_and_utf16_wire_escapes(tmp_path, monkeypatch):
    from collections import UserDict

    from contextvars import ContextVar

    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.native.connection import Connection

    monkeypatch.delenv("COMPUTER_USE_DEMO_FOREGROUND", raising=False)
    ledger = tmp_path / "native-json-wire"
    script = """
import json, pathlib, sys
session, ledger = sys.argv[1], pathlib.Path(sys.argv[2])
def send(id, result):
    print(json.dumps(dict(version=6, session=session, id=id, result=result)), flush=True)
hello = json.loads(sys.stdin.readline())
send(hello["id"], dict(platform="macos", globalInputAllowed=True, maxTimeoutMs=120000,
                       capabilities=["cancel", "requestDedup", "nativeOperations"]))
with ledger.open("wb") as output:
    for _ in range(2):
        line = sys.stdin.buffer.readline()
        output.write(line)
        output.flush()
        command = json.loads(line)
        send(command["id"], {"cleanup": "complete"})
    output.write(sys.stdin.buffer.read())
"""
    session = Connection(
        lambda: None,
        {"command": sys.executable, "args": ["-u", "-c", script, SESSION, str(ledger)]},
        allow_global_input=True,
    )
    context = ContextVar("mapping-context")

    class Mapping(UserDict):
        def __iter__(self):
            assert context.get() == "owner"
            assert asyncio.current_task() is owner
            return super().__iter__()

    owner = None
    params = {"operation": "fixture", "plain": "🦀"}
    mutable = {"operation": "fixture"}

    async def scenario():
        nonlocal owner
        owner = asyncio.current_task()
        context.set("owner")
        def edit(event):
            if event["kind"] == "command_start":
                event["params"]["mapping"] = Mapping({"\ud800": ["\udfff", 2.0, True], 3: "三"})

        try:
            assert await session.request("finish", params, {"timeoutMs": 3000}) == {"cleanup": "complete"}
            set_diagnostic_handler(edit)
            assert await session.request("finish", mutable, {"timeoutMs": 3000}) == {"cleanup": "complete"}
            with pytest.raises(ComputerUseError) as invalid:
                await session.request("finish", {**params, "bad": float("inf")}, {"timeoutMs": 3000})
            assert invalid.value.detail.startswith("Out of range float values are not JSON compliant;")
        finally:
            set_diagnostic_handler(None)
            if session.session_identity is not None:
                session.close()
            await session.wait_closed()

    asyncio.run(scenario())
    raw = ledger.read_bytes().splitlines()
    assert len(raw) == 2
    assert b'"plain":"\\ud83e\\udd80"' in raw[0]
    assert b'"mapping":{"\\ud800":["\\udfff",2.0,true],"3":"\\u4e09"}' in raw[1]
    assert json.loads(raw[1])["params"]["mapping"] == {"\ud800": ["\udfff", 2.0, True], "3": "三"}
