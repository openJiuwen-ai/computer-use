# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rust-owned macOS node replies and their diagnostic views."""

import json


def _callback_body(raw):
    """Encode only a callback's ordinary JSON values without coercing custom objects."""
    try:
        pending = [raw]
        seen = set()
        while pending:
            item = pending.pop()
            kind = type(item)
            if kind not in (dict, list):
                if kind not in (str, int, float, bool, type(None)):
                    return None
                continue
            identity = id(item)
            if identity in seen:
                continue
            seen.add(identity)
            if kind is dict:
                if any(type(key) is not str for key in item):
                    return None
                pending.extend(item.values())
            else:
                pending.extend(item)
        return json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8", "backslashreplace"
        )
    except (TypeError, ValueError, RuntimeError, MemoryError):
        return None


class NativeReadFrame:
    """The Rust frame and the raw view requested by an ordinary diagnostic callback."""

    __slots__ = ("_native", "_nodes", "_raw")

    def __init__(self, native):
        self._native = native
        self._raw = self._nodes = None

    @property
    def native(self):
        return self._native

    def capture(self, scope_kind, features):
        if self._raw is None:
            return self._native.capture(scope_kind, features)
        if self._native.has_semantic_urls:
            # The semantic URL view interprets the complete current result.
            current = self._raw
        else:
            # The admitted query and initial node index remain authoritative.
            # Replacing the raw node list does not replace those indexed rows.
            current = [{**node, "id": identity} for identity, node in self._nodes.items()]
        body = _callback_body(current)
        if body is None:
            raise ValueError("protocol: diagnostic observation edits are not JSON values")
        return self._native.capture(scope_kind, features, semantic_body=body)

    def diagnostic_bytes(self):
        return self._native.jsonl_diagnostic_bytes()

    def raw_result(self):
        if self._raw is None:
            raw = self._native.raw_result()
            self._nodes = {node["id"]: node for node in raw["nodes"]}
            self._raw = raw
        return self._raw


class NativeReadCapture:
    """A capture owned by the native macOS read engine."""

    __slots__ = ("capture",)

    def __init__(self, capture):
        self.capture = capture
