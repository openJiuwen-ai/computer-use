# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe session invalidation and actual driver retirement from host callbacks."""

import asyncio
import json
import os
import signal
import sys
import time
from contextvars import ContextVar
from pathlib import Path

from sdk_migration_replay_driver import SESSION, reply

HELLO = {"version": 6, "id": 0, "method": "hello", "params": {"globalInputAllowed": False}}
FINISH = {"operation": "transport:lease"}


def emit(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
    stream.flush()


def records(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


async def until(predicate):
    async with asyncio.timeout(6):
        while not predicate():
            await asyncio.sleep(0.005)


CASES = ("malformed-reentrant", "deadline-forced", "explicit-forced", "idle-forced", "hello-timeout")
CONTEXT = ContextVar("transport-lifecycle-context", default="launch")


def process_running(pid):
    if sys.platform == "win32":
        # os.kill(pid, 0) can terminate a Windows child rather than probe it.
        import _winapi

        try:
            process = _winapi.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        except OSError as error:
            if error.winerror == 87:  # ERROR_INVALID_PARAMETER: no such process
                return False
            raise
        try:
            return _winapi.WaitForSingleObject(process, 0) == _winapi.WAIT_TIMEOUT
        finally:
            _winapi.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def driver(case, output):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with (output / "wire.jsonl").open("xb", buffering=0) as journal:
        hello = json.loads(sys.stdin.buffer.readline())
        emit(journal, hello)
        assert hello == HELLO
        if case == "hello-timeout":
            assert not sys.stdin.buffer.readline()
            time.sleep(8)
            return
        emit(
            sys.stdout.buffer,
            {
                "version": 6,
                "session": SESSION,
                "id": 0,
                "result": reply(hello, writes=0),
            },
        )
        if case == "idle-forced":
            stop = time.monotonic() + 5
            while not (output / "idle-disconnect").exists():
                assert time.monotonic() < stop, "host did not release the idle driver"
                time.sleep(0.005)
            os.close(sys.stdout.fileno())
        else:
            command = json.loads(sys.stdin.buffer.readline())
            timeout = command.pop("timeoutMs")
            emit(journal, command)
            assert type(timeout) is int and 0 < timeout <= 3000
            (output / "holding").touch()
            if case == "malformed-reentrant":
                sys.stdout.buffer.write(b'{"malformed JSON"}\n')
                sys.stdout.buffer.flush()
            cancel = json.loads(sys.stdin.buffer.readline())
            emit(journal, cancel)
        assert not sys.stdin.buffer.readline()
    if case != "malformed-reentrant":
        # A real, still-running child gives the diagnostic sink an external
        # ordering witness. No GUI, driver mocks, or wall-clock comparisons.
        time.sleep(8)


async def exercise(case, output):
    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.errors import ComputerUseError
    from computer_use.native.connection import Connection

    output.mkdir()
    observed, invalidations = [], []
    owner = asyncio.current_task()
    stdout = requester = pid = None
    forced = []
    invalidation_errors = []
    hello_error_shared = None

    def lane():
        task = asyncio.current_task()
        if task is None:
            return "timer"
        if task is owner:
            return "owner"
        if task is requester:
            return "requester"
        if task is stdout:
            return "stdout"
        return "retirement"

    def snapshot(kind):
        observed.append([kind, CONTEXT.get(), lane(), connection.session_identity, len(invalidations)])

    def invalidate():
        snapshot("invalidate")
        invalidation_errors.append(sys.exception())
        invalidations.append(connection.session_identity)
        CONTEXT.set(f"after-invalidate-{len(invalidations)}")

    def on_event(event):
        nonlocal stdout, pid
        kind = event["kind"]
        if kind == "connection":
            pid = event["pid"]
        elif kind == "protocol_hello":
            stdout = asyncio.current_task()
        elif kind == "command_end":
            snapshot(kind)
            CONTEXT.set("after-error")
            if case == "malformed-reentrant":
                connection.close(RuntimeError("inner close must not replace the pending request's failure"))
        elif kind == "request_cancelling":
            snapshot(kind)
        elif kind == "driver_forced_exit":
            snapshot(kind)
            forced.append((event["pid"] == pid, process_running(event["pid"])))

    set_diagnostic_handler(on_event)
    connection = Connection(
        invalidate,
        driver={"command": sys.executable, "args": [__file__, "driver", case, str(output)]},
        allow_global_input=False,
    )

    def failure(error):
        return [
            error.code,
            error.effect,
            error.target,
            error.detail,
            type(error.__cause__).__name__,
            str(error.__cause__),
        ]

    async def request():
        CONTEXT.set("request")
        try:
            await connection.request("finish", FINISH, {"timeoutMs": 150 if case == "deadline-forced" else 3000})
        except ComputerUseError as error:
            return failure(error)
        raise AssertionError("A missing terminal response must fail")

    try:
        if case == "hello-timeout":
            try:
                await connection.connect(250)
            except ComputerUseError as error:
                outcome = failure(error)
                hello_error_shared = [error is previous for previous in invalidation_errors]
            else:
                raise AssertionError("The driver did not send hello")
        else:
            await connection.connect(3000)
            assert connection.session_identity == (0, SESSION)
            assert stdout is not None and stdout is not owner
            if case == "idle-forced":
                (output / "idle-disconnect").touch()
                await until(lambda: bool(invalidations))
                outcome = None
            else:
                requester = asyncio.create_task(request())
                if case == "explicit-forced":
                    await until(lambda: (output / "holding").exists())
                    CONTEXT.set("explicit-close")
                    connection.close(RuntimeError("explicit close with command pending"))
                outcome = await requester
    finally:
        if connection.session_identity is not None:
            connection.close()
        await connection.wait_closed()
        set_diagnostic_handler(None)

    assert all(row[3] is None for row in observed if row[0] != "request_cancelling"), observed
    command_sent = case not in ("idle-forced", "hello-timeout")
    assert [row[0] for row in observed].count("command_end") == int(command_sent), observed
    if command_sent:
        assert outcome[:3] == ["execution_unknown", "possible", "finish"], outcome
    if case == "malformed-reentrant":
        assert invalidations == [None, None] and not forced, observed
        assert "Expecting ':' delimiter" in outcome[3] and "inner close" not in outcome[3], outcome
        assert outcome[4] == "JSONDecodeError", outcome
        assert [row[:3] for row in observed] == [
            ["command_end", "launch", "stdout"],
            ["invalidate", "after-error", "stdout"],
            ["invalidate", "after-invalidate-1", "stdout"],
        ], observed
    else:
        assert invalidations == [None] * (2 if case == "hello-timeout" else 1), observed
        assert forced == [(True, True)], (observed, forced)
        if outcome is not None:
            assert outcome[4] == "RuntimeError", outcome
        assert observed[-1][:3] == ["driver_forced_exit", "after-invalidate-1", "retirement"], observed
        assert not process_running(pid), "wait_closed returned before the driver was reaped"
    if case == "hello-timeout":
        assert outcome[:3] == ["execution_failed", "none", "hello"], outcome
        assert "protocol hello timed out" in outcome[3], outcome
        assert [row[:3] for row in observed[:2]] == [
            ["invalidate", "launch", "timer"],
            ["invalidate", "launch", "owner"],
        ], observed
        assert hello_error_shared == [False, True], hello_error_shared
    wire = records(output / "wire.jsonl")
    expected = [HELLO]
    if command_sent:
        expected += [
            {"version": 6, "session": SESSION, "id": 1, "method": "finish", "params": FINISH},
            {"version": 6, "session": SESSION, "cancel": 1},
        ]
    assert wire == expected, wire
    report = {"observed": observed, "outcome": outcome, "wire": wire, "forced": forced}
    if hello_error_shared is not None:
        report["helloErrorShared"] = hello_error_shared
    return report


async def matrix(source, output):
    from computer_use.native import connection

    assert Path(connection.__file__).resolve().is_relative_to(source)
    output.mkdir(mode=0o700)
    # Each case mutates ContextVars in its owner as part of the contract.
    # A fresh Task keeps one case's close from changing the next launch.
    report = {}
    for case in CASES:
        report[case] = await asyncio.create_task(exercise(case, output / case))
    save(output / "report.json", report)


if __name__ == "__main__":
    if sys.argv[1] == "driver":
        driver(sys.argv[2], Path(sys.argv[3]))
    else:
        asyncio.run(matrix(Path(sys.argv[1]).resolve(), Path(sys.argv[2])))
