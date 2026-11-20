# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check main's encoded diagnostic route and ordinary callback on the same facts."""

import asyncio
import json
import tempfile
from io import BytesIO
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import DEFAULT, Mock, patch

from computer_use import diagnostics as sdk_diagnostics
from computer_use.diagnostics import diagnostic, pipeline_diagnostic, set_diagnostic_handler
from computer_use.pipeline.core import Effect, Event
from computer_use_mcp.diagnostics import Journal
from computer_use_mcp.runtime import Runtime, _runs
from computer_use_mcp.worker_transport import (
    FRAMED_PROTOCOL,
    EncodedObservationDiagnostic,
    EncodedPipelineDiagnostic,
    WorkerMessageWriter,
    _encode_message,
    read_message,
)

BODY = rb'{"kind":"observation","native":{"path":"a\/b","value":"\u4e2d"}}'
OBSERVATION = {
    "timestamp": "2026-11-04T03:00:00.000Z",
    "kind": "command_end",
    "traceId": "native-trace",
    "id": 12,
    "method": "inspect",
    "durationMs": 52.5,
    "target": None,
    "status": "ok",
}
TRACE_FIELDS = {
    "timestamp": OBSERVATION["timestamp"],
    "kind": "pipeline",
    "callId": "control-1",
    "path": ['界面 "内部"', "line\n2"],
    "phase": "exit",
    "effects": ["pure"],
    "attempt": 2,
    "branch": False,
    "cleanup": None,
    "dispatch": None,
    "errorType": None,
}


def worker_writer():
    frames = BytesIO()
    return frames, WorkerMessageWriter(frames, canonical_pipeline=True)


async def read_frames(payload, *, encoded_pipeline=False):
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    result = []
    while (message := await read_message(reader, encoded_pipeline=encoded_pipeline)) is not None:
        result.append(message)
    return result


def journal_record(journal, frame):
    if isinstance(frame, EncodedPipelineDiagnostic):
        journal.record_pipeline(frame.line, frame.byte_count)
    else:
        journal.record({**frame["diagnostic"], "executionId": frame.get("executionId")})


class ObservationDiagnosticTransportTests(IsolatedAsyncioTestCase):
    def isolate_runtime(self):
        self.enterContext(
            patch.multiple(
                "computer_use_mcp.runtime",
                require_image_delivery=DEFAULT,
                set_execution_guard=DEFAULT,
                set_action_handler=DEFAULT,
                set_text_page_handler=DEFAULT,
                set_observation_handler=DEFAULT,
                set_preview_handler=DEFAULT,
            )
        )
        self.enterContext(
            patch.multiple(
                "computer_use.diagnostics", _handler=None, _observation_diagnostic=None, _pipeline_transport=None
            )
        )

    async def test_encoded_pipeline_journals_same_bytes_with_custom_fallback(self):
        encoded_frames, optimized = worker_writer()
        ordinary_frames = BytesIO()
        ordinary = WorkerMessageWriter(ordinary_frames)
        event = Event(
            tuple(TRACE_FIELDS["path"]), "exit", frozenset({Effect.PURE}), call_id="control-1", attempt=2, branch=False
        )
        with patch("computer_use_mcp.worker_transport._pipeline_timestamp", return_value=TRACE_FIELDS["timestamp"]):
            self.assertTrue(optimized.send_pipeline(event, 3))
            self.assertTrue(optimized.send_pipeline(event, None))
        ordinary.send({"diagnostic": TRACE_FIELDS, "executionId": 3})
        ordinary.send({"diagnostic": TRACE_FIELDS})
        custom = {"diagnostic": {**TRACE_FIELDS, "extra": {"custom": "\ud800"}}}
        optimized.send(custom)
        ordinary.send(custom)
        optimized.flush()
        ordinary.flush()
        actual = await read_frames(encoded_frames.getvalue(), encoded_pipeline=True)
        expected = await read_frames(ordinary_frames.getvalue())
        self.assertEqual(len(actual), 3)
        self.assertTrue(all(isinstance(frame, EncodedPipelineDiagnostic) for frame in actual[:2]))
        self.assertEqual(actual[2], expected[2])
        with tempfile.TemporaryDirectory() as directory:
            baseline, rendered = Journal(directory), Journal(directory)
            for left, right in zip(expected, actual, strict=True):
                journal_record(baseline, left)
                journal_record(rendered, right)
            self.assertEqual(baseline._pending_bytes, rendered._pending_bytes)
            for journal in (baseline, rendered):
                journal.record({"timestamp": TRACE_FIELDS["timestamp"], "kind": "execution_end"})
            self.assertEqual(
                baseline.path.read_bytes().split(b"\n", 1)[1], rendered.path.read_bytes().split(b"\n", 1)[1]
            )

    async def test_origin_trace_preserves_callback_switches_and_nonowner_marshalling(self):
        self.isolate_runtime()

        async def emit(enabled):
            frames, writer = worker_writer()
            observed = []
            send = Mock(wraps=writer.send_pipeline) if enabled else None
            runtime = Runtime(writer.send, computer_factory=lambda: None, send_pipeline=send)
            original = sdk_diagnostics._handler

            def custom(record):
                observed.append((record["timestamp"], record["branch"]))
                original({**record, "phase": "hooked\ud800", "extra": ["界"]})

            token = _runs.set({"id": 43})
            trace = Event(("read", "unicode\n雪"), "exit", frozenset({Effect.PURE}), branch=True)
            try:
                for branch in (True, 1):
                    pipeline_diagnostic(Event(trace.path, trace.phase, trace.effects, branch=branch))
                await asyncio.to_thread(pipeline_diagnostic, trace)
                set_diagnostic_handler(custom)
                pipeline_diagnostic(trace)
                await asyncio.to_thread(pipeline_diagnostic, trace)
                set_diagnostic_handler(original)
                pipeline_diagnostic(trace)
                writer.flush()
            finally:
                _runs.reset(token)
                await runtime.close()
                set_diagnostic_handler(None)
            records = await read_frames(frames.getvalue(), encoded_pipeline=True)
            rows = [
                json.loads(frame.line)
                if isinstance(frame, EncodedPipelineDiagnostic)
                else {**frame["diagnostic"], "executionId": frame.get("executionId")}
                for frame in records
            ]
            return rows, observed, send.call_count if send is not None else 0

        with (
            patch("computer_use.diagnostics._pipeline_timestamp") as clock,
            patch("computer_use_mcp.worker_transport._pipeline_timestamp", clock),
        ):
            timestamps = [f"2026-11-04T03:00:0{second}.000Z" for second in range(1, 7)]
            clock.side_effect = iter(timestamps)
            reference = await emit(False)
            clock.side_effect = iter(timestamps)
            result = await emit(True)
        self.assertEqual(result[:2], reference[:2])
        self.assertEqual(len(result[0]), 6)
        self.assertIs(type(result[0][0]["branch"]), bool)
        self.assertIs(type(result[0][1]["branch"]), int)
        self.assertEqual(len(result[1]), 2)
        self.assertEqual(result[2], 2 if sdk_diagnostics._PIPELINE_STACK_SUPPORTED else 0)

    async def test_worker_reuses_validated_native_frame_bytes_with_no_body_serialization(self):
        source = {"diagnostic": OBSERVATION, "executionId": 3}
        with patch("computer_use_mcp.worker_transport.json.dumps", wraps=json.dumps) as encode:
            frames = _encode_message(source, FRAMED_PROTOCOL, observation_json=BODY)
            self.assertEqual(encode.call_count, 1)
            self.assertNotIn("result", encode.call_args.args[0])
        (opaque,) = await read_frames(frames)
        self.assertIsInstance(opaque, EncodedObservationDiagnostic)
        self.assertEqual(opaque.result_json, BODY)
        self.assertEqual(json.loads(opaque.result_json.tobytes()), json.loads(BODY))

    async def test_custom_diagnostic_handler_receives_plain_mutable_result_without_stale_source_bytes(self):
        self.isolate_runtime()
        frames, messages = worker_writer()
        runtime = Runtime(messages.send, send_observation=messages.send_observation)
        self.addAsyncCleanup(runtime.close)
        capture = SimpleNamespace(diagnostic_bytes=lambda: BODY, raw_result=lambda: json.loads(BODY))

        def redact(event):
            self.assertIs(type(event["result"]), dict)
            event["result"]["native"]["path"] = "redacted"
            runtime.diagnostic(event)

        diagnostic(OBSERVATION, _native_observation=capture)
        set_diagnostic_handler(redact)
        diagnostic(OBSERVATION, _native_observation=capture)
        original_frame, redacted_frame = await read_frames(frames.getvalue())
        self.assertEqual(original_frame.result_json, BODY)
        self.assertEqual(json.loads(redacted_frame.result_json.tobytes())["native"]["path"], "redacted")
