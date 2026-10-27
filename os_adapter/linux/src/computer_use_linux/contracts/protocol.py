"""Protocol 6 framing, cancellation and bounded request execution."""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field


def token(prefix="ref"):
    return f"{prefix}:{uuid.uuid4().hex}"


def now():
    return time.time() * 1000


class Failure(Exception):
    def __init__(
        self,
        detail,
        code="execution_failed",
        target="desktop",
        effect="none",
        # The error contract exposes a next field and accepts the matching keyword.
        # pylint: disable-next=redefined-builtin
        next="Read the target again before continuing.",
    ):
        super().__init__(detail)
        self.code, self.target, self.effect, self.next = code, target, effect, next

    def wire(self):
        return dict(code=self.code, target=self.target, effect=self.effect, detail=str(self), next=self.next)


def require(condition, detail, code="invalid_request"):
    if not condition:
        raise Failure(detail, code=code)


def fields(value, required, optional=()):
    require(
        isinstance(value, dict) and set(required) <= value.keys() and value.keys() <= set(required) | set(optional),
        "Invalid object fields.",
    )
    return value


def integer(value, minimum=0, maximum=9007199254740991):
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    require(type(value) is int and minimum <= value <= maximum, "Invalid integer.")
    return value


def string(value, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())), "Expected a nonempty string.")
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "Invalid Unicode.")
    return value


def finite(value):
    require(type(value) in (int, float), "Expected a finite number.")
    try:
        representable = math.isfinite(value)
    except OverflowError:
        representable = False
    require(representable, "Expected a finite native number.")
    return value


def gap(notes, message):
    if notes is not None and len(notes) < 128:
        notes.append(str(message)[:4096])


KEYS = (
    {
        "Return",
        "Tab",
        "Escape",
        "BackSpace",
        "Delete",
        "space",
        "Left",
        "Right",
        "Up",
        "Down",
        "Home",
        "End",
        "Page_Up",
        "Page_Down",
    }
    | {f"F{i}" for i in range(1, 13)}
    | set("-=[]\\;',./`")
)
MODIFIERS = {"super", "ctrl", "alt", "shift"}


def key_parts(value):
    parts = string(value).split("+")
    require(
        (parts[-1] in KEYS or re.fullmatch("[a-z0-9]", parts[-1]))
        and len(set(parts[:-1])) == len(parts[:-1])
        and set(parts[:-1]) <= MODIFIERS,
        "Unsupported key or modifiers.",
    )
    return parts


@dataclass
class Request:
    deadline: float
    stopped: threading.Event
    cancelled: threading.Event = field(default_factory=threading.Event)
    effect: str = "none"
    context: dict | None = None
    pump: object = None
    target: str = "desktop"

    def check(self):
        if self.stopped.is_set() or self.cancelled.is_set():
            raise Failure("Request cancelled.", "cancelled", effect=self.effect)
        if time.monotonic() >= self.deadline:
            raise Failure("Request deadline exceeded.", "deadline_exceeded", effect=self.effect)

    def sent(self):
        self.check()
        if self.effect == "none":
            self.effect = "possible"

    def pause(self, seconds=0.02):
        self.check()
        if self.pump:
            self.pump()
        self.stopped.wait(min(seconds, max(0, self.deadline - time.monotonic())))
        self.check()

    @property
    def remaining_ms(self):
        self.check()
        return max(1, int((self.deadline - time.monotonic()) * 1000))


@contextmanager
def desktop_lease(ctx, identity):
    import fcntl
    import os
    import stat

    # Do not trust another user's runtime directory or follow a pre-created symlink.
    root = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/computer-use-{os.getuid()}"
    os.makedirs(root, mode=0o700, exist_ok=True)
    info = os.stat(root, follow_symlinks=False)
    require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o022 == 0,
        "Unsafe desktop lock directory.",
    )
    name = hashlib.sha256(identity.encode()).hexdigest()[:24]
    fd = os.open(os.path.join(root, f"computer-use-{name}.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "Unsafe desktop lock file.")
        while True:
            ctx.check()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                ctx.pause()
        yield
    finally:
        os.close(fd)
