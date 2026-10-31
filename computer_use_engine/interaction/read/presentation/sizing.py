# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exact structured JSON byte accounting without materializing its full encoding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import is_dataclass
from types import MappingProxyType
from typing import Any, Iterable

from computer_use.json import dumps, utf8
from computer_use.protocol.observation import NativeObservation, encode_observation

from .content import DisplayText


class DocumentSize:
    """Count each occurrence, reusing lengths of shared immutable source records.

    The memo retains its objects only for this measurement. Repeated references
    still contribute their full encoded length, preserving cache admission.
    """

    def __init__(self) -> None:
        self.memo: dict[int, tuple[object, int]] = {}
        self.strings: dict[str, int] = {}

    def record(self, pairs: Iterable[tuple[str, object]]) -> int:
        size, separator = 2, 0
        for key, value in pairs:
            size += separator + self.data(str(key)) + 1 + self.data(value)
            separator = 1
        return size

    def data(self, value: Any) -> int:
        if isinstance(value, str):
            if value not in self.strings:
                self.strings[value] = len(utf8(dumps(value)))
            return self.strings[value]
        if value is None or value is True:
            return 4
        if value is False:
            return 5
        if type(value) is int:
            return len(str(value))
        if type(value) is float:
            return len(utf8(dumps(value)))
        cached = self.memo.get(id(value))
        if cached is not None:
            return cached[1]
        if type(value) in (dict, MappingProxyType):
            size = self.record(value.items())
        elif isinstance(value, NativeObservation):
            size = self.data(encode_observation(value))
        elif isinstance(value, DisplayText) and value.kind == "details_hint":
            prepared, ref = value.values
            size = self.record((("kind", value.kind), ("ref", ref), ("observed", prepared.snapshot.facts["observed"])))
        elif is_dataclass(value):
            size = self.record((name, getattr(value, name)) for name in value.__dataclass_fields__)
        elif isinstance(value, Mapping):
            size = self.record(value.items())
        elif isinstance(value, (tuple, list, set, frozenset)):
            size = 2 + max(0, len(value) - 1) + sum(self.data(item) for item in value)
        else:
            size = len(utf8(dumps(value)))
        self.memo[id(value)] = (value, size)
        return size

    def document(self, document: Any) -> int:
        from .document import DisplayTree

        parts = []
        for part in document.parts:
            if isinstance(part, DisplayTree):
                snapshot = part.prepared.snapshot
                parts.append(
                    self.record(
                        (
                            ("kind", "tree"),
                            ("scope", part.scope),
                            ("indent", part.indent),
                            ("details", part.details),
                            ("nodes", part.nodes),
                            ("native", snapshot.native),
                            ("facts", snapshot.facts),
                            ("refs", snapshot.refs),
                            ("annotations", snapshot.annotations),
                        )
                    )
                )
            else:
                parts.append(self.data(part))
        # {"version":1,"context":<data>,"parts":[<data>, ...]}
        return (
            self.record((("version", 1), ("context", document.context)))
            + 1
            + self.data("parts")
            + 1
            + 2
            + max(0, len(parts) - 1)
            + sum(parts)
        )
