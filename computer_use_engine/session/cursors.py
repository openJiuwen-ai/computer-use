"""Bounded opaque continuations, bound to a native source and a read method."""

from collections import OrderedDict
from dataclasses import dataclass
from uuid import uuid4

from computer_use.diagnostics import localFailure


@dataclass(frozen=True)
class TextContinuation:
    target: object
    value: str
    position: int
    source: object = None


class TextCursors:
    def __init__(self, capacity=128, max_characters=8 * 1024 * 1024):
        self.capacity, self.max_characters = capacity, max_characters
        self._entries = OrderedDict()

    def publish(self, target, value, position, source=None):
        if len(value) > self.max_characters:
            raise localFailure(
                "text exceeds the bounded public capture cache",
                "read a smaller explicit text region",
                "observation_incomplete",
            )
        key = "text_" + uuid4().hex
        self._entries[key] = TextContinuation(target, value, position, source)
        while (
            len(self._entries) > self.capacity
            or sum({id(entry.value): len(entry.value) for entry in self._entries.values()}.values())
            > self.max_characters
        ):
            self._entries.popitem(last=False)
        return key

    def resolve(self, key, target):
        if not key.startswith("text_"):
            raise localFailure("this cursor is not a text continuation", "use the cursor returned by readText()")
        entry = self._entries.get(key)
        if entry is None:
            raise localFailure(
                "text continuation expired", "readText() without a cursor to start a new snapshot", "snapshot_expired"
            )
        if entry.target != target:
            raise localFailure(
                "text cursor belongs to a different element or window", "use the cursor on its original element"
            )
        return entry
