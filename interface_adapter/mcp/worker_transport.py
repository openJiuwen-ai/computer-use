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
import re
import socket
from asyncio import IncompleteReadError, get_running_loop
from dataclasses import dataclass
from types import MappingProxyType, NoneType

from computer_use.diagnostics import _pipeline_timestamp
from computer_use.json import loads
from computer_use.pipeline.core import Effect, Event

from .diagnostics import dumps

MAX_MESSAGE_BYTES = 128 * 1024 * 1024
HEADER_BYTES = 4
DIAGNOSTIC_BATCH_BYTES = 64 * 1024
PROTOCOL_ENV = "COMPUTER_USE_WORKER_PROTOCOL"
PIPELINE_TAG_ENV = "COMPUTER_USE_WORKER_PIPELINE_TAG"
FRAMED_PROTOCOL = "framed-v2"
_OBSERVATION_FRAME = b"\x00"
_DIGEST_BYTES = 32
_MAX_DIAGNOSTIC_HEADER_BYTES = 64 * 1024
# The frame's capped size leaves its highest bit free. Only the worker's
# canonical trace encoder sets it, without changing admission or batch sizes.
_PIPELINE_TAG = 1 << 31
_PIPELINE_ENVELOPE = b'{"diagnostic":'
_PIPELINE_TIMESTAMP = _PIPELINE_ENVELOPE + b'{"timestamp":'
_TIMESTAMP_START = len(_PIPELINE_TIMESTAMP)
_TIMESTAMP_END = _TIMESTAMP_START + 26  # Quoted UTC timestamp with millisecond precision.
_TIMESTAMP_PATTERN = re.compile(rb'"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"')
_JOURNAL_TIMESTAMP = _PIPELINE_TIMESTAMP[len(_PIPELINE_ENVELOPE):].decode("ascii")
_JOURNAL_SUFFIX_START = _TIMESTAMP_END - len(_PIPELINE_ENVELOPE)
_PIPELINE_MEMO_LIMIT = 512
_PIPELINE_MEMO_PAYLOAD_BYTES = 4096
_PIPELINE_FIELDS = (
    "timestamp", "kind", "callId", "path", "phase", "effects", "attempt", "branch", "cleanup", "dispatch", "errorType"
)
_PIPELINE_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
_FAST_PIPELINE_START = HEADER_BYTES + _TIMESTAMP_START
_FAST_PIPELINE_END = HEADER_BYTES + _TIMESTAMP_END


@dataclass(frozen=True, slots=True)
class EncodedObservationDiagnostic:
    """Validated routing fields and the exact JSON bytes for one native result."""

    event: MappingProxyType
    header_json: bytes
    result_json: memoryview


@dataclass(frozen=True, slots=True)
class EncodedPipelineDiagnostic:
    """One validated standard trace ready for the journal's existing batch."""

    line: str
    byte_count: int


def _canonical_trace_field(value):
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return True
    # Reused trace bytes require built-in strings with standard JSON behavior.
    # pylint: disable-next=unidiomatic-typecheck
    return kind is list and all(type(part) is str for part in value)


def _canonical_pipeline_message(message):
    event = message.get("diagnostic")
    # Keep dictionary subclasses with custom behavior on the regular path.
    # pylint: disable-next=unidiomatic-typecheck
    if type(message) is not dict or type(event) is not dict:
        return None
    if tuple(message) not in (("diagnostic",), ("diagnostic", "executionId")) or tuple(event) != _PIPELINE_FIELDS:
        return None
    # Memoization depends on keys that use built-in string equality.
    # pylint: disable-next=unidiomatic-typecheck
    if any(type(key) is not str for key in (*message, *event)):
        return None
    if not all(_canonical_trace_field(value) for value in event.values()):
        return None
    execution_id = message.get("executionId")
    # Positive integer IDs and booleans have different wire encodings.
    # pylint: disable-next=unidiomatic-typecheck
    if execution_id is not None and (type(execution_id) is not int or execution_id <= 0):
        return None
    try:
        # Strict encoding rejects surrogate code units, which require the
        # existing compatibility encoder and normal journal serialization.
        payload = _PIPELINE_ENCODER.encode(message).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if not _TIMESTAMP_PATTERN.fullmatch(payload, _TIMESTAMP_START, _TIMESTAMP_END):
        return None
    return payload


def _pipeline_message(event, timestamp, execution_id):
    record = {
        "timestamp": timestamp,
        "kind": "pipeline",
        "callId": event.call_id,
        "path": list(event.path),
        "phase": event.phase,
        "effects": sorted(effect.value for effect in event.effects),
        "attempt": event.attempt,
        "branch": event.branch,
        "cleanup": event.cleanup,
        "dispatch": event.dispatch,
        "errorType": event.error_type,
    }
    message = {"diagnostic": record}
    if execution_id is not None:
        message["executionId"] = execution_id
    return message


def _encoded_pipeline_diagnostic(payload, message):
    event = message.get("diagnostic")
    invalid = "Invalid canonical pipeline diagnostic."
    valid_timestamp = payload.startswith(_PIPELINE_TIMESTAMP) and _TIMESTAMP_PATTERN.fullmatch(
        payload, _TIMESTAMP_START, _TIMESTAMP_END
    )
    if not valid_timestamp or b"\n" in payload or b"\r" in payload:
        raise ValueError(invalid)
    if not isinstance(event, dict) or event.get("kind") != "pipeline" or tuple(event) != _PIPELINE_FIELDS:
        raise ValueError(invalid)
    if not all(_canonical_trace_field(value) for value in event.values()):
        raise ValueError(invalid)

    execution_id = message.get("executionId")
    if execution_id is None:
        suffix = b',"executionId":null}'
    # Execution IDs are positive built-in integers.
    # pylint: disable-next=unidiomatic-typecheck
    elif type(execution_id) is int and execution_id > 0:
        suffix = b',"executionId":' + str(execution_id).encode("ascii") + b"}"
    else:
        raise ValueError("Invalid canonical pipeline execution ID.")
    if "executionId" in message:
        if len(message) != 2 or not payload.endswith(suffix):
            raise ValueError("Invalid canonical pipeline diagnostic envelope.")
        event_end = len(payload) - len(suffix)
    else:
        if len(message) != 1 or not payload.endswith(b"}}"):
            raise ValueError("Invalid canonical pipeline diagnostic envelope.")
        event_end = len(payload) - 1
    if payload[event_end - 1] != ord("}"):
        raise ValueError("Invalid canonical pipeline diagnostic envelope.")
    # The private tag comes only from the canonical encoder. Move its
    # execution ID into the journal envelope without repeating serialization.
    body_start, body_end = len(_PIPELINE_ENVELOPE), event_end - 1
    line = b"".join((memoryview(payload)[body_start:body_end], suffix, b"\n"))
    return EncodedPipelineDiagnostic(line.decode("utf-8"), len(line))


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


def _observation_payload(message, observation_json=None):
    """Encode complete native facts once, separately from small routing fields."""
    event = message.get("diagnostic") if isinstance(message, dict) else None
    if not isinstance(event, dict) or event.get("kind") != "command_end" or event.get("method") != "inspect":
        return None
    if event.get("status") != "ok":
        return None
    result = event.get("result")
    encoded_result_only = observation_json is not None and "result" not in event
    if not encoded_result_only and (not isinstance(result, dict) or result.get("kind") != "observation"):
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
        if encoded_result_only:
            result_json = observation_json
        else:
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
    return b"".join((_OBSERVATION_FRAME, digest.digest(), header_size, header_json)), result_json


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


def _message_size(size, *, canonical_pipeline=False):
    if not 0 < size <= MAX_MESSAGE_BYTES:
        raise ValueError("Internal worker message exceeds the 128 MiB admission limit.")
    if canonical_pipeline:
        size |= _PIPELINE_TAG
    return size.to_bytes(HEADER_BYTES, "big")


def _encode_message(message, protocol, *, observation_json=None, canonical_pipeline=False):
    if protocol != FRAMED_PROTOCOL:
        raise RuntimeError("Unsupported internal worker protocol: " + protocol)
    if canonical_pipeline:
        payload = _canonical_pipeline_message(message)
        if payload is not None:
            return _message_size(len(payload), canonical_pipeline=True) + payload
    observation = _observation_payload(message, observation_json)
    if observation is None:
        if observation_json is not None:
            event = message["diagnostic"]
            message = {**message, "diagnostic": {**event, "result": loads(observation_json)}}
        payload = dumps(message).encode("utf-8", "backslashreplace")
        return _message_size(len(payload)) + payload
    header, result = observation
    return b"".join((_message_size(len(header) + len(result)), header, result))


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

    def __init__(self, writer, *, protocol=FRAMED_PROTOCOL, canonical_pipeline=False):
        self.writer = writer
        self.protocol = protocol
        self.canonical_pipeline = canonical_pipeline
        self._pipeline_templates = {}
        self._timestamp_token = ("", b"")
        self.pending = bytearray()
        self.scheduled = None

    def send(self, message):
        event = message.get("diagnostic")
        pipeline = isinstance(event, dict) and event.get("kind") == "pipeline"
        frame = _encode_message(message, self.protocol)
        self._send_frame(frame, pipeline)

    def send_pipeline(self, event, execution_id):
        """Reuse the exact encoded frame except for this event's own timestamp."""
        if self.protocol != FRAMED_PROTOCOL or not self.canonical_pipeline:
            return False
        # Only the built-in event contract is used to identify reusable bytes.
        # pylint: disable-next=unidiomatic-typecheck
        if type(event) is not Event:
            return False
        # Preserve the integer form in the frame and in the cache key.
        # pylint: disable-next=unidiomatic-typecheck
        if execution_id is not None and (type(execution_id) is not int or execution_id <= 0):
            return False
        path, effects = event.path, event.effects
        call, phase, attempt = event.call_id, event.phase, event.attempt
        branch, cleanup, dispatch, error = event.branch, event.cleanup, event.dispatch, event.error_type
        # Cache keys need exact types and standard hashing and serialization.
        # pylint: disable-next=unidiomatic-typecheck
        if type(path) is not tuple or any(type(part) is not str for part in path):
            return False
        # pylint: disable-next=unidiomatic-typecheck
        if type(effects) is not frozenset or any(type(effect) is not Effect for effect in effects):
            return False
        # pylint: disable-next=unidiomatic-typecheck
        if type(call) not in (str, NoneType) or type(phase) is not str or type(attempt) not in (int, NoneType):
            return False
        if type(branch) not in (bool, int, str, NoneType):
            return False
        if any(type(value) not in (str, NoneType) for value in (cleanup, dispatch, error)):
            return False
        key = (call, path, phase, effects, attempt, type(branch), branch, cleanup, dispatch, error, execution_id)
        templates = self._pipeline_templates
        template = templates.get(key)
        # Pipeline diagnostics used to stamp each event when its callback ran.
        # Preserve that clock boundary for buffered native stages as well.
        timestamp = _pipeline_timestamp()
        if template is None:
            message = _pipeline_message(event, timestamp, execution_id)
            frame = _encode_message(message, self.protocol, canonical_pipeline=True)
            can_cache = len(frame) <= HEADER_BYTES + _PIPELINE_MEMO_PAYLOAD_BYTES and frame[0] & 128
            if (
                can_cache
                and frame.startswith(_PIPELINE_TIMESTAMP, HEADER_BYTES)
                and _TIMESTAMP_PATTERN.fullmatch(frame, _FAST_PIPELINE_START, _FAST_PIPELINE_END)
            ):
                if len(templates) >= _PIPELINE_MEMO_LIMIT:
                    templates.clear()
                templates[key] = (frame[:_FAST_PIPELINE_START], frame[_FAST_PIPELINE_END:])
            self._send_frame(frame, True)
            return True
        cached_timestamp, token = self._timestamp_token
        if timestamp != cached_timestamp:
            token = b""
            # Only a built-in timestamp string can reuse the exact JSON token.
            # pylint: disable-next=unidiomatic-typecheck
            if type(timestamp) is str and len(timestamp) == 24 and timestamp.isascii():
                candidate = ('"' + timestamp + '"').encode("ascii")
                if _TIMESTAMP_PATTERN.fullmatch(candidate) is not None:
                    token = candidate
            self._timestamp_token = (timestamp, token)
        if token:
            self._send_frame(b"".join((template[0], token, template[1])), True)
            return True
        # The same timestamp was already obtained. Serialize the ordinary
        # record with it when an unusual clock formatting cannot splice bytes.
        self.send(_pipeline_message(event, timestamp, execution_id))
        return True

    def _send_frame(self, frame, pipeline):
        if pipeline and len(frame) <= DIAGNOSTIC_BATCH_BYTES:
            if len(self.pending) + len(frame) > DIAGNOSTIC_BATCH_BYTES:
                self.flush()
            self.pending.extend(frame)
            if self.scheduled is None:
                self.scheduled = get_running_loop().call_soon(self.flush)
        else:
            self.flush()
            self.writer.write(frame)

    def send_observation(self, message, observation_json):
        frame = _encode_message(message, self.protocol, observation_json=observation_json)
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


async def read_message(reader, *, protocol=FRAMED_PROTOCOL, encoded_pipeline=False, pipeline_lines=None):
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
    tagged_pipeline = encoded_pipeline and bool(size & _PIPELINE_TAG)
    if tagged_pipeline:
        size ^= _PIPELINE_TAG
    if not 0 < size <= MAX_MESSAGE_BYTES:
        raise ValueError("Invalid internal worker message size (maximum 128 MiB).")
    payload = await reader.readexactly(size)
    if not tagged_pipeline and payload.startswith(_OBSERVATION_FRAME):
        return _decode_observation_payload(payload)
    tail = None
    can_reuse = tagged_pipeline and pipeline_lines is not None and size <= _PIPELINE_MEMO_PAYLOAD_BYTES
    if (
        can_reuse
        and payload.startswith(_PIPELINE_TIMESTAMP)
        and _TIMESTAMP_PATTERN.fullmatch(payload, _TIMESTAMP_START, _TIMESTAMP_END)
    ):
        tail = payload[_TIMESTAMP_END:]
        cached = pipeline_lines.get(tail)
        if cached is not None:
            suffix, byte_count = cached
            timestamp = payload[_TIMESTAMP_START:_TIMESTAMP_END].decode("ascii")
            return EncodedPipelineDiagnostic(_JOURNAL_TIMESTAMP + timestamp + suffix, byte_count)
    message = loads(payload)
    if not isinstance(message, dict):
        raise TypeError("Internal worker message must be a JSON object.")
    if tagged_pipeline:
        diagnostic = _encoded_pipeline_diagnostic(payload, message)
        # Only the worker's exact canonical spelling is memoized. Other tagged
        # forms, including overwritten duplicate keys, keep the original parser.
        if tail is not None and _canonical_pipeline_message(message) == payload:
            if len(pipeline_lines) >= _PIPELINE_MEMO_LIMIT:
                pipeline_lines.clear()
            pipeline_lines[tail] = (diagnostic.line[_JOURNAL_SUFFIX_START:], diagnostic.byte_count)
        return diagnostic
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
