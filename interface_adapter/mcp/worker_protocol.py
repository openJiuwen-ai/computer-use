"""Parent-selected messages on the private worker connection.

The native protocol permits 64 MiB replies. A full command diagnostic can thus
exceed the stream's 12 MiB flow-control watermark. A length prefix separates
message admission from that watermark without truncating observations or logs.
The larger logical cap allows a native reply plus its diagnostic envelope.
Public MCP stdio and the Windows worker authentication remain unchanged.
An already imported older parent starts a worker without a version variable;
that worker retains the original JSONL contract until the parent is upgraded.
"""

import os
from asyncio import IncompleteReadError, get_running_loop

from computer_use._json import loads

from .diagnostic_json import dumps

MAX_MESSAGE_BYTES = 128 * 1024 * 1024
HEADER_BYTES = 4
DIAGNOSTIC_BATCH_BYTES = 64 * 1024
PROTOCOL_ENV = "COMPUTER_USE_WORKER_PROTOCOL"
FRAMED_PROTOCOL = "framed-v1"
LEGACY_PROTOCOL = "jsonl-v1"


def consume_protocol():
    """Consume the parent's version before opening the private channel.

    Absence means an old, already imported parent using JSONL. Consuming the
    variable prevents that inherited version from affecting nested workers.
    Unknown versions are refused before reading or dispatching any code.
    """
    protocol = os.environ.pop(PROTOCOL_ENV, LEGACY_PROTOCOL)
    if protocol not in (FRAMED_PROTOCOL, LEGACY_PROTOCOL):
        raise RuntimeError("Unsupported internal worker protocol: " + protocol)
    return protocol


def _encode_message(message, protocol):
    payload = dumps(message).encode("utf-8", "backslashreplace")
    if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
        raise ValueError("Internal worker message exceeds the 128 MiB admission limit.")
    if protocol == FRAMED_PROTOCOL:
        return len(payload).to_bytes(HEADER_BYTES, "big") + payload
    if protocol == LEGACY_PROTOCOL:
        return payload + b"\n"
    raise RuntimeError("Unsupported internal worker protocol: " + protocol)


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
    if protocol == LEGACY_PROTOCOL:
        line = await reader.readline()
        return loads(line) if line else None
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
    message = loads(await reader.readexactly(size))
    if not isinstance(message, dict):
        raise TypeError("Internal worker message must be a JSON object.")
    return message
