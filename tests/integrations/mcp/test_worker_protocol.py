"""Private diagnostic transport must not turn a large observation into a reset."""

import asyncio
import os
import unittest
from unittest.mock import patch

from computer_use._json import dumps
from computer_use_mcp import worker_protocol
from computer_use_mcp.worker_protocol import (
    DIAGNOSTIC_BATCH_BYTES,
    FRAMED_PROTOCOL,
    HEADER_BYTES,
    LEGACY_PROTOCOL,
    MAX_MESSAGE_BYTES,
    PROTOCOL_ENV,
    WorkerMessageWriter,
    readMessage,
    workerProtocol,
    writeMessage,
)


class BufferWriter:
    def __init__(self):
        self.data = bytearray()
        self.chunks = []

    def write(self, data):
        self.data.extend(data)
        self.chunks.append(len(data))


class WorkerProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_protocol_is_selected_by_parent_and_consumed_without_data_detection(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(workerProtocol(), LEGACY_PROTOCOL)
        with patch.dict(os.environ, {PROTOCOL_ENV: FRAMED_PROTOCOL}):
            self.assertEqual(workerProtocol(), FRAMED_PROTOCOL)
            self.assertNotIn(PROTOCOL_ENV, os.environ)
        with (
            patch.dict(os.environ, {PROTOCOL_ENV: "future-unknown"}),
            self.assertRaisesRegex(RuntimeError, "Unsupported internal worker protocol"),
        ):
            workerProtocol()

    async def test_legacy_jsonl_encoding_keeps_batched_pipeline_and_output_order(self):
        writer = BufferWriter()
        messages = WorkerMessageWriter(writer, protocol=LEGACY_PROTOCOL)
        trace = {"diagnostic": {"kind": "pipeline", "phase": "enter"}}
        output = {"output": {"type": "text", "text": "legacy\n测试"}}
        messages.send(trace)
        messages.send(output)
        reader = asyncio.StreamReader()
        reader.feed_data(writer.data)
        reader.feed_eof()
        self.assertEqual(await readMessage(reader, protocol=LEGACY_PROTOCOL), trace)
        self.assertEqual(await readMessage(reader, protocol=LEGACY_PROTOCOL), output)
        self.assertIsNone(await readMessage(reader, protocol=LEGACY_PROTOCOL))

    async def test_pipeline_batches_bound_transport_fragments_and_preserve_all_events(self):
        writer = BufferWriter()
        messages = WorkerMessageWriter(writer)
        count = 4000
        for index in range(count):
            messages.send({"diagnostic": {"kind": "pipeline", "index": index, "path": ["scope"] * 20}})
        self.assertLessEqual(len(messages.pending), DIAGNOSTIC_BATCH_BYTES)
        # Actual command progress flushes every preceding trace before itself.
        progress = {"diagnostic": {"kind": "command_start", "id": 5}}
        messages.send(progress)
        self.assertFalse(messages.pending)
        self.assertIsNone(messages.scheduled)
        self.assertLess(len(writer.chunks), count // 50)
        self.assertLessEqual(max(writer.chunks), DIAGNOSTIC_BATCH_BYTES)
        reader = asyncio.StreamReader(limit=64)
        reader.feed_data(writer.data)
        for index in range(count):
            self.assertEqual((await readMessage(reader))["diagnostic"]["index"], index)
        self.assertEqual(await readMessage(reader), progress)

    async def test_pipeline_flushes_on_yield_and_output_is_immediate(self):
        writer = BufferWriter()
        messages = WorkerMessageWriter(writer)
        messages.send({"diagnostic": {"kind": "pipeline"}})
        self.assertFalse(writer.data)
        await asyncio.sleep(0)
        self.assertTrue(writer.data)
        messages.send({"diagnostic": {"kind": "pipeline", "phase": "exit"}})
        output = {"output": {"type": "text", "text": "before a synchronous infinite loop"}}
        messages.send(output)
        self.assertFalse(messages.pending)
        reader = asyncio.StreamReader()
        reader.feed_data(writer.data)
        self.assertEqual((await readMessage(reader))["diagnostic"]["kind"], "pipeline")
        self.assertEqual((await readMessage(reader))["diagnostic"]["phase"], "exit")
        self.assertEqual(await readMessage(reader), output)

    async def test_messages_larger_than_reader_watermark_preserve_following_message(self):
        # A deliberately tiny stream watermark verifies that it is flow control,
        # not a message-size limit. Preserve full Unicode, newlines, and evidence.
        message = {"diagnostic": {"kind": "command_end", "result": {"nodes": ["测试\n" * 1000]}}}
        following = {"content": [{"type": "text", "text": "same execution continues"}]}
        writer = BufferWriter()
        writeMessage(writer, message)
        writeMessage(writer, following)
        reader = asyncio.StreamReader(limit=64)
        reader.feed_data(writer.data)
        reader.feed_eof()
        self.assertEqual(await readMessage(reader), message)
        self.assertEqual(await readMessage(reader), following)
        self.assertIsNone(await readMessage(reader))

    async def test_partial_header_and_body_are_not_clean_eof(self):
        for payload in (b"\x00\x00", (10).to_bytes(HEADER_BYTES, "big") + b"{}"):
            with self.subTest(payload=payload):
                reader = asyncio.StreamReader()
                reader.feed_data(payload)
                reader.feed_eof()
                with self.assertRaises(asyncio.IncompleteReadError):
                    await readMessage(reader)

    async def test_invalid_size_rejected_before_body_is_read(self):
        for size in (0, MAX_MESSAGE_BYTES + 1):
            with self.subTest(size=size):
                reader = asyncio.StreamReader()
                reader.feed_data(size.to_bytes(HEADER_BYTES, "big"))
                # No EOF/body: accepting the header would wait indefinitely.
                with self.assertRaisesRegex(ValueError, "message size"):
                    await readMessage(reader)

    async def test_admission_boundary_and_nonobject(self):
        writer = BufferWriter()
        message = {"value": "x"}
        size = len(dumps(message).encode("utf-8"))
        with patch.object(worker_protocol, "MAX_MESSAGE_BYTES", size):
            writeMessage(writer, message)
            reader = asyncio.StreamReader()
            reader.feed_data(writer.data)
            self.assertEqual(await readMessage(reader), message)
            rejected = BufferWriter()
            with self.assertRaisesRegex(ValueError, "admission limit"):
                writeMessage(rejected, {"value": "xx"})
            self.assertEqual(rejected.data, b"")
        reader = asyncio.StreamReader()
        reader.feed_data((2).to_bytes(HEADER_BYTES, "big") + b"[]")
        with self.assertRaisesRegex(TypeError, "JSON object"):
            await readMessage(reader)


if __name__ == "__main__":
    unittest.main()
