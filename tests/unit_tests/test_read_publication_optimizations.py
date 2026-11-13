# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import io
import json
import sys
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from computer_use import diagnostics
from computer_use.pipeline.core import Effect, Event
from computer_use_mcp.diagnostics import Journal
from computer_use_mcp.worker_transport import EncodedPipelineDiagnostic, WorkerMessageWriter, read_message


def test_mcp_repeated_traces_match_ordinary_journal_and_flush_before_progress(monkeypatch, tmp_path):
    times = ("2026-11-13T12:00:00.012Z", "2026-11-13T12:00:00.013Z", "2026-11-13T12:00:00.013Z")
    events = [
        Event(
            ("Read", "原始字段"),
            "enter",
            frozenset({Effect.PURE}),
            call_id="read",
            branch=branch,
            timestamp="2026-11-01T12:00:00.000Z",
        )
        for branch in (True, 1, True)
    ]
    monkeypatch.setattr(diagnostics, "_pipeline_timestamp", iter(times).__next__)
    regular = []
    try:
        diagnostics.set_diagnostic_handler(regular.append)
        for event in events:
            diagnostics.pipeline_diagnostic(event)
    finally:
        diagnostics.set_diagnostic_handler(None)
    assert [record["timestamp"] for record in regular] == list(times)

    from computer_use_mcp import worker_transport

    monkeypatch.setattr(worker_transport, "_pipeline_timestamp", iter(times).__next__)

    async def run():
        stream = io.BytesIO()
        worker = WorkerMessageWriter(stream, canonical_pipeline=True)
        for event in events:
            assert worker.send_pipeline(event, 2)
        worker.send({"ready": True})
        reader = asyncio.StreamReader()
        reader.feed_data(stream.getvalue())
        reader.feed_eof()
        expected, actual = Journal(tmp_path), Journal(tmp_path)
        lines = {}
        for event in regular:
            decoded = await read_message(reader, encoded_pipeline=True, pipeline_lines=lines)
            assert isinstance(decoded, EncodedPipelineDiagnostic)
            actual.record_pipeline(decoded.line, decoded.byte_count)
            expected.record({**event, "executionId": 2})
        assert await read_message(reader, encoded_pipeline=True, pipeline_lines=lines) == {"ready": True}
        assert await read_message(reader, encoded_pipeline=True, pipeline_lines=lines) is None
        marker = {"timestamp": times[-1], "kind": "execution_end"}
        for journal in (expected, actual):
            journal.record(marker)
        assert actual.path.read_bytes().splitlines()[1:] == expected.path.read_bytes().splitlines()[1:]

    asyncio.run(run())


def test_mcp_noncanonical_trace_uses_the_ordinary_worker_message(monkeypatch):
    from computer_use_mcp import worker_transport

    monkeypatch.setattr(worker_transport, "_pipeline_timestamp", lambda: "noncanonical timestamp")

    async def run():
        stream = io.BytesIO()
        worker = WorkerMessageWriter(stream, canonical_pipeline=True)
        event = Event(("Read",), "enter", frozenset({Effect.PURE}))
        assert worker.send_pipeline(event, 2)
        worker.flush()
        reader = asyncio.StreamReader()
        reader.feed_data(stream.getvalue())
        reader.feed_eof()
        message = await read_message(reader, encoded_pipeline=True, pipeline_lines={})
        assert message["diagnostic"]["timestamp"] == "noncanonical timestamp"
        assert message["executionId"] == 2

    asyncio.run(run())


def test_mcp_trace_keeps_the_ordinary_diagnostic_at_the_recursion_boundary(monkeypatch):
    from computer_use_mcp import worker_transport

    stamp = "2026-11-13T12:00:00.013Z"
    monkeypatch.setattr(diagnostics, "_pipeline_timestamp", lambda: stamp)
    monkeypatch.setattr(worker_transport, "_pipeline_timestamp", lambda: stamp)
    event = Event(("Read", "属性"), "enter", frozenset({Effect.PURE}))

    async def collect(optimized):
        stream = io.BytesIO()
        worker = WorkerMessageWriter(stream, canonical_pipeline=optimized)

        def handle(record):
            worker.send({"diagnostic": record, "executionId": 2})

        def send(record):
            return worker.send_pipeline(record, 2)

        def emit(depth):
            if depth:
                emit(depth - 1)
            else:
                diagnostics.pipeline_diagnostic(event)

        diagnostics.set_diagnostic_handler(handle)
        if optimized:
            diagnostics._register_pipeline_transport(handle, send)
        limit = sys.getrecursionlimit()
        try:
            # Exercise both a cached frame and the regular encoder when the
            # callback approaches the interpreter's recursion limit.
            sys.setrecursionlimit(160)
            sent_at = []
            for depth in range(161):
                worker.flush()
                before = stream.tell()
                try:
                    emit(depth)
                except RecursionError:
                    pass
                worker.flush()
                if stream.tell() != before:
                    sent_at.append(depth)
        finally:
            sys.setrecursionlimit(limit)
            diagnostics.set_diagnostic_handler(None)
        return sent_at

    original = asyncio.run(collect(False))
    assert original and original[-1] < 160
    assert asyncio.run(collect(True)) == original


def test_linux_replays_the_published_observation_without_another_encoding(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "os_adapter/linux/src"))
    from computer_use_linux.contracts.protocol import Request
    from computer_use_linux.read.native_observation import encode_observation
    from computer_use_linux.runtime.server import Server

    output = io.StringIO()
    server = Server(None, output=output)
    server.version, server.session = 6, "driver"
    server.backend = SimpleNamespace(pump=lambda: None)
    facts = {
        "session": "driver",
        "surface": "surface",
        "observed": {"startedAt": 1, "completedAt": 2, "id": "capture"},
        "roots": ["node"],
        "nodes": [{
            "node": "node",
            "requestedFields": ["name", "description", "accessibleId"],
            "values": {"name": "中文", "description": None},
            "fieldStatus": {"accessibleId": {"state": "absent"}},
            "actions": {"items": [], "state": "complete"},
        }],
        "relations": [{
            "source": "node",
            "property": "children",
            "targets": ["unread", "unread"],
            "state": "partial",
            "certainty": "confirmed",
            "cursor": "next",
            "offset": 0,
            "total": 3,
        }],
        "unread": ["unread"],
    }
    graph = encode_observation(facts)
    properties = graph["nodes"][0]["properties"]
    assert [item["key"][1] for item in properties] == ["name", "description", "accessibleId", "actions", "children"]
    assert properties[1]["value"]["data"] is None
    assert properties[2]["state"] == "absent" and properties[2]["value"] is None
    assert [item["data"]["ref"] for item in properties[4]["value"]["data"]["items"]] == ["unread", "unread"]
    assert graph["nodes"][1]["properties"] is None
    calls = []

    def perform(method, params, _request):
        calls.append((method, params))
        return graph

    server.native = SimpleNamespace(perform=perform)
    request = {"version": 6, "session": "driver", "id": 1, "method": "inspect", "params": {}, "timeoutMs": 3000}
    server.execute(request, Request(monotonic() + 3, server.stopped))
    first = output.getvalue()
    assert first == json.dumps(
        {"version": 6, "session": "driver", "id": 1, "result": graph},
        ensure_ascii=True, allow_nan=False, separators=(",", ":"),
    ) + "\n"

    def cannot_encode_observation(identifier, **reply):
        assert "result" not in reply
        return original_encode(identifier, **reply)

    original_encode = server.encode_reply
    monkeypatch.setattr(server, "encode_reply", cannot_encode_observation)
    server.execute(request, Request(monotonic() + 3, server.stopped))
    assert output.getvalue() == first * 2
    assert calls == [("inspect", {})]
    server.execute({**request, "params": {"changed": True}}, Request(monotonic() + 3, server.stopped))
    assert json.loads(output.getvalue().splitlines()[-1])["error"]["code"] == "request_conflict"
