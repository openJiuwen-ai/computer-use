# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Large native diagnostics retain source bytes without parent graph decoding."""

import asyncio
import hashlib
import json
import os
import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from computer_use_mcp.diagnostics import Journal
from computer_use_mcp.session import ExecutionSession
from computer_use_mcp.worker_transport import (
    FRAMED_PROTOCOL,
    PROTOCOL_ENV,
    EncodedObservationDiagnostic,
    WorkerMessageWriter,
    _encode_message,
    consume_protocol,
    read_message,
)


def message(result=None, execution=3):
    return {
        "executionId": execution,
        "diagnostic": {
            "timestamp": "2026-11-04T03:00:00.000Z",
            "kind": "command_end",
            "traceId": "native-trace",
            "id": 12,
            "method": "inspect",
            "durationMs": 52.5,
            "target": None,
            "status": "ok",
            "result": result or {"kind": "observation", "native": {"name": "中文🙂\ud800", "status": [1, True, None]}},
        },
    }


async def decode(frame):
    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()
    return await read_message(reader)


class ObservationDiagnosticTransportTests(IsolatedAsyncioTestCase):
    async def test_complete_native_bytes_and_prior_traces_reach_journal_in_order(self):
        original = message()
        frame = _encode_message(original, FRAMED_PROTOCOL)
        opaque = await decode(frame)
        self.assertIsInstance(opaque, EncodedObservationDiagnostic)
        self.assertEqual(opaque.event["executionId"], 3)
        self.assertEqual(opaque.event["traceId"], "native-trace")
        expected_result = json.dumps(
            original["diagnostic"]["result"], ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8", "backslashreplace")
        self.assertEqual(opaque.result_json, expected_result)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, COMPUTER_USE_LOG_DIR=directory):
            journal = Journal()
            journal.record({"kind": "pipeline", "phase": "before"})
            with patch("computer_use_mcp.diagnostics.dumps", side_effect=AssertionError("native graph reencoded")):
                journal.record_observation(opaque.header_json, opaque.result_json)
            journal.record({"kind": "execution_end", "executionId": 3})
            lines = journal.path.read_bytes().splitlines()
            self.assertEqual(json.loads(lines[1])["phase"], "before")
            self.assertEqual(lines[2], opaque.header_json[:-1] + b',"result":' + expected_result + b"}")
            self.assertEqual(json.loads(lines[2]), {**original["diagnostic"], "executionId": 3})
            self.assertEqual(json.loads(lines[3])["kind"], "execution_end")

    async def test_normal_error_and_output_frames_keep_their_contract(self):
        error = message()
        event = error["diagnostic"]
        event.pop("result")
        event.update(status="error", error="native failure", failure={"effect": "possible"})
        for original in (error, {"executionId": 3, "output": {"type": "text", "text": "after"}}):
            with self.subTest(message=original):
                self.assertEqual(await decode(_encode_message(original, FRAMED_PROTOCOL)), original)
        writer = SimpleNamespace(frames=[], write=lambda frame: writer.frames.append(frame))
        messages = WorkerMessageWriter(writer)
        messages.send({"diagnostic": {"kind": "pipeline", "phase": "before"}})
        messages.send(message())
        messages.send({"executionId": 3, "output": {"type": "text", "text": "after"}})
        reader = asyncio.StreamReader()
        reader.feed_data(b"".join(writer.frames))
        reader.feed_eof()
        self.assertEqual((await read_message(reader))["diagnostic"]["phase"], "before")
        self.assertIsInstance(await read_message(reader), EncodedObservationDiagnostic)
        self.assertEqual((await read_message(reader))["output"]["text"], "after")

    async def test_body_and_routing_header_are_bound_to_the_exact_frame(self):
        frame = _encode_message(message(), FRAMED_PROTOCOL)
        corrupted = frame[:-1] + bytes([frame[-1] ^ 1])
        with self.assertRaisesRegex(ValueError, "integrity"):
            await decode(corrupted)
        payload = bytearray(frame[4:])
        header_size = int.from_bytes(payload[33:37], "big")
        header = bytes(payload[37 : 37 + header_size]).replace(b'"executionId":3', b'"executionId":0')
        body = header_size.to_bytes(4, "big") + header + bytes(payload[37 + header_size :])
        altered = b"\x00" + hashlib.sha256(body).digest() + body
        with self.assertRaisesRegex(ValueError, "header"):
            await decode(len(altered).to_bytes(4, "big") + altered)
        with self.assertRaises(ValueError):
            await decode((1).to_bytes(4, "big") + b"\x00")
        with patch.dict(os.environ, {PROTOCOL_ENV: "framed-v1"}), self.assertRaises(RuntimeError):
            consume_protocol()

    async def test_session_binds_progress_and_persists_large_diagnostic_without_decoding_result(self):
        source = message({"kind": "observation", "native": "x" * (1024 * 1024)}, execution=1)["diagnostic"]
        start = {"kind": "command_start", "traceId": source["traceId"], "id": source["id"], "method": "inspect"}
        code = (
            "from computer_use.diagnostics import diagnostic\ndiagnostic("
            + repr(start)
            + ")\ndiagnostic("
            + repr(source)
            + ")\nprint('done')"
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, COMPUTER_USE_LOG_DIR=directory):
            session = ExecutionSession(5000)
            progress = []
            original_loads = json.loads
            parsed_sizes = []

            def parse_header_only(value):
                parsed_sizes.append(len(value))
                self.assertLess(len(value), 64 * 1024)
                return original_loads(value)

            try:
                with patch("computer_use_mcp.worker_transport.loads", side_effect=parse_header_only):
                    result = await session.execute(code, on_progress=lambda: progress.append(True))
                self.assertFalse(result.get("isError"), result)
                self.assertEqual(progress, [True, True])
                events = [json.loads(line) for line in session._journal.path.read_bytes().splitlines()]
                event = next(event for event in events if event["kind"] == "command_end")
                self.assertEqual(event["result"], source["result"])
                self.assertEqual(event["executionId"], 1)
                self.assertTrue(parsed_sizes)
            finally:
                await session.close()
