# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Private worker IPC: connection authentication, framing, and ordered writes.

The native protocol permits 64 MiB replies. A full command diagnostic can thus
exceed the stream's 12 MiB flow-control watermark. A length prefix separates
message admission from that watermark without truncating observations or logs.
The larger logical cap allows a native reply plus its diagnostic envelope.
Public MCP stdio and the Windows worker authentication remain unchanged.
Large native diagnostic results stay encoded across this private channel; the
parent validates their routing header and integrity before writing the journal.
"""

import asyncio
import hashlib
import json
import os
import socket
from asyncio import IncompleteReadError, get_running_loop
from dataclasses import dataclass
from types import MappingProxyType

from computer_use.json import loads

from .diagnostics import dumps

MAX_MESSAGE_BYTES = 128 * 1024 * 1024
HEADER_BYTES = 4
DIAGNOSTIC_BATCH_BYTES = 64 * 1024
PROTOCOL_ENV = "COMPUTER_USE_WORKER_PROTOCOL"
FRAMED_PROTOCOL = "framed-v2"
_OBSERVATION_FRAME = b"\x00"
_DIGEST_BYTES = 32
_MAX_DIAGNOSTIC_HEADER_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class EncodedObservationDiagnostic:
    """Validated routing fields and the exact JSON bytes for one native result."""

    event: MappingProxyType
    header_json: bytes
    result_json: memoryview


def _validate_observation_header(event):
    required = {"timestamp", "kind", "method", "status", "traceId", "id", "executionId"}
    invalid = "Invalid encoded native-observation diagnostic header."
    if not isinstance(event, dict) or not required <= event.keys():
        raise ValueError(invalid)
    if "result" in event or "error" in event or "failure" in event:
        raise ValueError(invalid)
    if event["kind"] != "command_end" or event["method"] != "inspect" or event["status"] != "ok":
        raise ValueError(invalid)
    for key in ("timestamp", "traceId"):
        if not isinstance(event[key], str) or not event[key]:
            raise ValueError(invalid)
    if type(event["id"]) is not int or event["id"] <= 0:
        raise ValueError(invalid)
    execution_id = event["executionId"]
    if execution_id is not None and (type(execution_id) is not int or execution_id <= 0):
        raise ValueError(invalid)


def _observation_payload(message):
    """Encode complete native facts once, separately from small routing fields."""
    event = message.get("diagnostic") if isinstance(message, dict) else None
    if not isinstance(event, dict) or event.get("kind") != "command_end" or event.get("method") != "inspect":
        return None
    if event.get("status") != "ok":
        return None
    result = event.get("result")
    if not isinstance(result, dict) or result.get("kind") != "observation":
        return None
    if message.keys() - {"diagnostic", "executionId"}:
        return None
    header = {key: value for key, value in event.items() if key != "result"}
    header["executionId"] = message.get("executionId")
    try:
        _validate_observation_header(header)
        header_json = json.dumps(header, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8", "backslashreplace"
        )
        if len(header_json) > _MAX_DIAGNOSTIC_HEADER_BYTES:
            return None
        result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8", "backslashreplace"
        )
    except (TypeError, ValueError):
        # A custom diagnostic still follows the ordinary serializer contract.
        return None
    header_size = len(header_json).to_bytes(HEADER_BYTES, "big")
    digest = hashlib.sha256(header_size)
    digest.update(header_json)
    digest.update(result_json)
    return _OBSERVATION_FRAME + digest.digest() + header_size + header_json + result_json


def _decode_observation_payload(payload):
    view = memoryview(payload)
    body = view[len(_OBSERVATION_FRAME) + _DIGEST_BYTES:]
    if len(body) < HEADER_BYTES:
        raise ValueError("Truncated encoded native-observation diagnostic.")
    digest = payload[len(_OBSERVATION_FRAME): len(_OBSERVATION_FRAME) + _DIGEST_BYTES]
    if hashlib.sha256(body).digest() != digest:
        raise ValueError("Encoded native-observation diagnostic integrity mismatch.")
    header_size = int.from_bytes(body[:HEADER_BYTES], "big")
    if not 0 < header_size <= _MAX_DIAGNOSTIC_HEADER_BYTES or HEADER_BYTES + header_size >= len(body):
        raise ValueError("Invalid encoded native-observation diagnostic header size.")
    header_json = bytes(body[HEADER_BYTES: HEADER_BYTES + header_size])
    event = loads(header_json)
    _validate_observation_header(event)
    # These exact header bytes become the journal's metadata. The result slice
    # stays opaque; both were bound by the checked digest and enclosing frame.
    return EncodedObservationDiagnostic(MappingProxyType(event), header_json, body[HEADER_BYTES + header_size:])


def consume_protocol():
    """Consume the parent's version before opening the private channel.

    Consuming the variable prevents that inherited version from affecting
    nested workers. Obsolete formats are refused before dispatching code.
    """
    protocol = os.environ.pop(PROTOCOL_ENV, FRAMED_PROTOCOL)
    if protocol != FRAMED_PROTOCOL:
        raise RuntimeError("Unsupported internal worker protocol: " + protocol)
    return protocol


def _encode_message(message, protocol):
    if protocol != FRAMED_PROTOCOL:
        raise RuntimeError("Unsupported internal worker protocol: " + protocol)
    payload = _observation_payload(message)
    if payload is None:
        payload = dumps(message).encode("utf-8", "backslashreplace")
    if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
        raise ValueError("Internal worker message exceeds the 128 MiB admission limit.")
    return len(payload).to_bytes(HEADER_BYTES, "big") + payload


def write_message(writer, message, *, protocol=FRAMED_PROTOCOL):
    """Enqueue one complete message in one stream write."""
    writer.write(_encode_message(message, protocol))


class WorkerMessageWriter:
    """Coalesce synchronous pipeline traces without delaying outputs or progress.

    A large tree can emit thousands of leaf-Pass events before yielding. Some
    asyncio transports sum their queued fragment sizes on every write, making
    one write per event quadratic while the event loop cannot drain the socket.
    Preserve every frame, but enqueue pipeline traces in bounded byte batches.
    Other records flush the batch first and are sent immediately in order.
    """

    def __init__(self, writer, *, protocol=FRAMED_PROTOCOL):
        self.writer = writer
        self.protocol = protocol
        self.pending = bytearray()
        self.scheduled = None

    def send(self, message):
        frame = _encode_message(message, self.protocol)
        event = message.get("diagnostic")
        if isinstance(event, dict) and event.get("kind") == "pipeline" and len(frame) <= DIAGNOSTIC_BATCH_BYTES:
            if len(self.pending) + len(frame) > DIAGNOSTIC_BATCH_BYTES:
                self.flush()
            self.pending.extend(frame)
            if self.scheduled is None:
                self.scheduled = get_running_loop().call_soon(self.flush)
        else:
            self.flush()
            self.writer.write(frame)

    def flush(self):
        if self.scheduled is not None:
            self.scheduled.cancel()
            self.scheduled = None
        if self.pending:
            payload = bytes(self.pending)
            self.pending.clear()
            self.writer.write(payload)


async def read_message(reader, *, protocol=FRAMED_PROTOCOL):
    """Read one admitted message; only an EOF between messages is a clean close."""
    if protocol != FRAMED_PROTOCOL:
        raise RuntimeError("Unsupported internal worker protocol: " + protocol)
    try:
        header = await reader.readexactly(HEADER_BYTES)
    except IncompleteReadError as error:
        if not error.partial:
            return None
        raise
    size = int.from_bytes(header, "big")
    if not 0 < size <= MAX_MESSAGE_BYTES:
        raise ValueError("Invalid internal worker message size (maximum 128 MiB).")
    payload = await reader.readexactly(size)
    if payload.startswith(_OBSERVATION_FRAME):
        return _decode_observation_payload(payload)
    message = loads(payload)
    if not isinstance(message, dict):
        raise TypeError("Internal worker message must be a JSON object.")
    return message


async def worker_connection():
    port = os.environ.pop("COMPUTER_USE_WORKER_PORT", None)
    if port is not None:
        token = os.environ.pop("COMPUTER_USE_WORKER_TOKEN", "")
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            raise RuntimeError("Invalid internal worker authentication.")
        reader, writer = await asyncio.open_connection("127.0.0.1", int(port), limit=12 * 1024 * 1024)
        try:
            writer.write((token + "\t" + str(os.getpid()) + "\n").encode("ascii"))
            await writer.drain()
        except BaseException:
            writer.close()
            raise
        return reader, writer
    ipc = socket.socket(fileno=3)
    try:
        ipc.setblocking(False)
        return await asyncio.open_connection(sock=ipc, limit=12 * 1024 * 1024)
    except BaseException:
        ipc.close()
        raise
