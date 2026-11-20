# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""Deterministic idle v6 transport fixture. No desktop access."""

import asyncio
import json
import os
import select
import sys
import time
from pathlib import Path

SESSION = "d8ff8abb-b1c4-4e5c-8c52-382a2c572561"
BINDING = {"session": SESSION, "instance": None, "rootWindow": None, "surface": None, "node": None, "capture": None}
COMMANDS = [
    ("hello", {"globalInputAllowed": False}),
    ("begin", {"intent": {"kind": "read", "parameters": {}}, "mode": "read", "target": BINDING}),
    ("finish", {"operation": "read-lease"}),
]
PREVIEW = [
    {"kind": "frame", "name": "fixture", "png": None, "extra": "retained"},
    {"kind": "reset", "extra": "retained"},
]


def write_line(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
    handle.flush()


def wait_marker(path):
    stop = time.monotonic() + 8
    while not path.exists():
        assert time.monotonic() < stop, path
        readable, _, _ = select.select([sys.stdin], [], [], 0.010)
        if readable:
            assert not sys.stdin.buffer.readline(), "unexpected native command while idle"
            raise AssertionError("stdin closed before expected idle command")


def driver(output):
    preview_fd = int(os.environ["COMPUTER_USE_PREVIEW_FD"])
    assert preview_fd == 3
    with (output / "wire.jsonl").open("xb", buffering=0) as wire, os.fdopen(preview_fd, "wb", buffering=0) as preview:
        for request_id, (method, params) in enumerate(COMMANDS):
            request = json.loads(sys.stdin.buffer.readline())
            timeout = request.pop("timeoutMs", None)
            expected = {"version": 6, "id": request_id, "method": method, "params": params}
            if request_id:
                assert type(timeout) is int and 0 < timeout <= 30000
                expected["session"] = SESSION
            assert request == expected
            write_line(wire, request)
            results = (
                {
                    "platform": "macos",
                    "globalInputAllowed": False,
                    "maxTimeoutMs": 30000,
                    "capabilities": ["cancel", "requestDedup", "nativeOperations", "nativeDirectory"],
                },
                {"operation": "read-lease", "target": BINDING, "remainingMs": 30000},
                {"cleanup": "complete"},
            )
            write_line(
                sys.stdout.buffer, {"version": 6, "id": request_id, "session": SESSION, "result": results[request_id]}
            )

        wait_marker(output / "diagnose")
        write_line(sys.stderr.buffer, {"requestId": 2, "message": {"fixture": "idle café"}})
        write_line(sys.stderr.buffer, {"requestId": 99, "message": ["unlinked", "idle"]})
        sys.stderr.buffer.write(b"not-json\n")
        sys.stderr.buffer.flush()

        wait_marker(output / "preview")
        preview.write(b'{"kind":"frame","name":"invalid","png":false}\n')
        preview.write(b"{ malformed\n")
        for event in PREVIEW:
            write_line(preview, event)
        preview.write(b'{"kind":"frame","name":"unterminated","png":null}')

        wait_marker(output / "exit")
        sys.stderr.buffer.write(b"cleanup-without-newline")
        sys.stderr.buffer.flush()
    raise SystemExit(17)


async def until(predicate):
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(0.005)


async def exercise(source, output):
    from computer_use.diagnostics import set_diagnostic_handler
    from computer_use.errors import ComputerUseError
    from computer_use.native import connection
    from computer_use.native.preview import set_preview_handler

    assert Path(connection.__file__).resolve().is_relative_to(source.resolve())
    initial, idle, previews, replacement_previews, invalidations = [], [], [], [], []
    set_diagnostic_handler(initial.append)
    set_preview_handler(previews.append)
    output.mkdir()
    c = connection.Connection(
        lambda: invalidations.append((c.session_identity, c.session)),
        driver={"command": sys.executable, "args": [__file__, "driver", str(output)]},
        allow_global_input=False,
    )
    try:
        hello = await c.connect(3000)
        begin = await c.request("begin", COMMANDS[1][1], {"timeoutMs": 3000})
        finish = await c.request("finish", COMMANDS[2][1], {"timeoutMs": 3000})
        active = c.session_identity
        assert active == (0, SESSION) and not invalidations
        (pid,) = (event["pid"] for event in initial if event["kind"] == "connection")
        (trace,) = (event["traceId"] for event in initial if event["kind"] == "command_start" and event["id"] == 2)

        set_diagnostic_handler(idle.append)
        set_preview_handler(replacement_previews.append)
        (output / "diagnose").touch()
        await until(lambda: len(idle) == 3)
        assert c.session_identity == active
        assert not previews and not replacement_previews

        (output / "preview").touch()
        await until(lambda: len(previews) == 2)
        assert previews == PREVIEW and not replacement_previews
        assert c.session_identity == active
        assert not invalidations

        with c.retain_session(active):
            (output / "exit").touch()
            await until(lambda: bool(invalidations) and len(idle) == 4)
            assert invalidations == [(None, None)] and c.session_identity is None
            try:
                await c.request("finish", COMMANDS[2][1], {"timeoutMs": 3000})
            except ComputerUseError as error:
                stale = {key: getattr(error, key) for key in ("code", "target", "effect", "detail")}
                assert stale["code"] == "stale_target" and stale["effect"] == "none"
            else:
                raise AssertionError("retained operation revived an exited driver")
        await c.wait_closed()
        await asyncio.gather(*tuple(getattr(c, "_readers", ())))
        assert previews == PREVIEW and not replacement_previews
        assert len(initial) > 0 and all(e["kind"] != "backend_diagnostic" for e in initial)
        assert idle[-1]["kind"] == "local_error"
        projected = []
        for event in idle[:-1]:
            assert event["kind"] == "backend_diagnostic" and event["pid"] == pid
            record = {k: v for k, v in event.items() if k not in {"pid", "timestamp", "traceId"}}
            if "traceId" in event:
                assert event["traceId"] == trace
                record["traceLink"] = "finish-2"
            projected.append(record)
        assert projected == [
            {"kind": "backend_diagnostic", "value": {"fixture": "idle café"}, "requestId": 2, "traceLink": "finish-2"},
            {"kind": "backend_diagnostic", "value": ["unlinked", "idle"], "requestId": 99},
            {"kind": "backend_diagnostic", "value": "not-json"},
            {"kind": "backend_diagnostic", "value": "cleanup-without-newline", "partial": True},
        ]
        wire = [json.loads(line) for line in (output / "wire.jsonl").read_bytes().splitlines()]
        report = {
            "hello": hello,
            "begin": begin,
            "finish": finish,
            "wire": wire,
            "diagnostic": projected,
            "preview": previews,
            "invalidations": invalidations,
            "retainedRequestError": stale,
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        if c.session_identity is not None:
            c.close()
        await c.wait_closed()
        set_diagnostic_handler(None)
        set_preview_handler(None)


if __name__ == "__main__":
    if sys.argv[1] == "driver":
        driver(Path(sys.argv[2]))
    else:
        asyncio.run(exercise(Path(sys.argv[1]), Path(sys.argv[2])))
