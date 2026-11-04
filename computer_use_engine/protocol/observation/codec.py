# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize the normalized observation model independently of driver wire data."""

from __future__ import annotations

import math
import re
import types
from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from .model import ElementValue, FieldPath, NativeObservation, ReadIssue, SpecialValue
from .validation import record_types, resolved_type, validate_query_observation

_SAFE_INT = 2**53 - 1
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


class _EscapedMap(dict):
    """A user mapping whose literal keys include the semantic tag marker."""


def _error(path: FieldPath, detail: str) -> ValueError:
    return ValueError(f"observation: {'.'.join(map(str, path)) or '<root>'}: {detail}")


class _Decoder:
    """Decode typed records within one bounded document scope."""

    def __init__(self) -> None:
        self.remaining = 2_000_000

    @staticmethod
    def _tag(value: Any, path: FieldPath) -> Any:
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(value) is float and not math.isfinite(value):
            raise _error(path, "special numbers require a semantic tag")
        # pylint: disable-next=unidiomatic-typecheck
        if type(value) is not dict or "$type" not in value:
            return value
        kind = value.get("$type")
        if kind == "mixed" and set(value) == {"$type"}:
            return SpecialValue.MIXED
        if kind == "element" and set(value) == {"$type", "node"} and isinstance(value["node"], str):
            return ElementValue(value["node"])
        if kind == "datetime" and set(value) == {"$type", "value"} and isinstance(value["value"], str):
            try:
                result = datetime.fromisoformat(value["value"])
            except ValueError as exc:
                raise _error(path, "invalid datetime") from exc
            if result.tzinfo is None or result.utcoffset() is None:
                raise _error(path, "datetime requires a timezone")
            return result
        if kind == "float" and set(value) == {"$type", "value"} and value["value"] in ("nan", "inf", "-inf", "-0"):
            return -0.0 if value["value"] == "-0" else float(value["value"])
        matches_kind = (
            True
            if (
                kind == "integer"
                and set(value) == {"$type", "value"}
                and isinstance(value["value"], str)
                and _INTEGER.fullmatch(value["value"])
            )
            else False
        )
        if matches_kind:
            try:
                return int(value["value"])
            except ValueError as exc:
                raise _error(path, "integer exceeds the supported digit budget") from exc
        # pylint: disable-next=unidiomatic-typecheck
        if kind == "map" and set(value) == {"$type", "entries"} and type(value["entries"]) is list:
            mapping = _EscapedMap()
            for pair in value["entries"]:
                invalid_mapping = (
                    True
                    # pylint: disable-next=unidiomatic-typecheck
                    if type(pair) is not list or len(pair) != 2 or not isinstance(pair[0], str) or pair[0] in mapping
                    else False
                )
                if invalid_mapping:
                    raise _error(path, "invalid escaped mapping entry")
                mapping[pair[0]] = pair[1]
            return mapping
        raise _error(path, "unknown or malformed semantic tag")

    def value(self, value: Any, expected: Any, path: FieldPath = (), depth: int = 0, *, decoded: bool = False) -> Any:
        self.remaining -= 1
        if self.remaining < 0 or depth > 96:
            raise _error(path, "record structure budget exceeded")
        if not decoded:
            value = self._tag(value, path)
        expected = resolved_type(expected)
        origin, args = get_origin(expected), get_args(expected)
        if origin in (Union, types.UnionType):
            for choice in args:
                try:
                    return self.value(value, choice, path, depth + 1, decoded=True)
                except ValueError:
                    pass
            raise _error(path, "value does not match its declared type")
        if origin is Literal:
            if any(type(value) is type(choice) and value == choice for choice in args):
                return value
        # pylint: disable-next=unidiomatic-typecheck
        elif expected is type(None):
            if value is None:
                return None
        elif expected in (str, bool, int, float):
            if type(value) in ((int, float) if expected is float else (expected,)):
                return value
        elif expected is datetime:
            if isinstance(value, datetime):
                return value
        elif isinstance(expected, type) and issubclass(expected, Enum):
            if isinstance(value, expected):
                return value
        # pylint: disable-next=unidiomatic-typecheck
        elif origin is tuple and type(value) is list:
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(self.value(item, args[0], (*path, index), depth + 1) for index, item in enumerate(value))
            if len(value) == len(args):
                return tuple(
                    self.value(item, item_type, (*path, index), depth + 1)
                    for index, (item, item_type) in enumerate(zip(value, args))
                )
        elif origin is Mapping and isinstance(value, dict):
            return {
                self.value(key, args[0], (*path, "<key>"), depth + 1): self.value(
                    item, args[1], (*path, key), depth + 1
                )
                for key, item in value.items()
            }
        elif isinstance(expected, type) and is_dataclass(expected):
            # pylint: disable-next=unidiomatic-typecheck
            if type(value) is expected:
                return value
            # pylint: disable-next=unidiomatic-typecheck
            if type(value) is dict:
                hints = record_types(expected)
                required = {
                    item.name
                    for item in fields(expected)
                    if item.default is MISSING and item.default_factory is MISSING
                }
                if not required <= value.keys() or value.keys() - hints.keys():
                    raise _error(path, "unexpected or missing record fields")
                return expected(
                    **{key: self.value(item, hints[key], (*path, key), depth + 1) for key, item in value.items()}
                )
        raise _error(path, "invalid field value")


def _encode(value: Any) -> Any:
    if isinstance(value, ElementValue):
        return {"$type": "element", "node": value.node}
    if isinstance(value, SpecialValue):
        return {"$type": value.value}
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is int and abs(value) > _SAFE_INT:
        return {"$type": "integer", "value": str(value)}
    matches_value = (
        True
        # pylint: disable-next=unidiomatic-typecheck
        if type(value) is float and (not math.isfinite(value) or value == 0 and math.copysign(1, value) < 0)
        else False
    )
    if matches_value:
        return {"$type": "float", "value": "-0" if value == 0 else str(value)}
    if is_dataclass(value):
        result = {}
        for item in fields(value):
            content = getattr(value, item.name)
            if item.default is not MISSING and content == item.default:
                continue
            if item.default_factory is not MISSING and content == item.default_factory():
                continue
            result[item.name] = _encode(content)
        return result
    if isinstance(value, Mapping):
        if "$type" in value:
            return {"$type": "map", "entries": [[key, _encode(item)] for key, item in value.items()]}
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def decode_observation(value: Any) -> NativeObservation:
    """Restore a normalized observation from its public semantic encoding.

    Args:
        value: Parsed JSON with kind ``native_observation`` and model fields.

    Returns:
        Immutable records whose graph edges retain driver-owned node identities.

    Raises:
        ValueError: The document uses another format, violates field types, or
            contains invalid references, ranges, or coordinate spaces.
    """
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is not dict or value.get("kind") != "native_observation":
        raise ValueError("observation: expected normalized native_observation format")
    observation = _Decoder().value({key: item for key, item in value.items() if key != "kind"}, NativeObservation)
    validate_query_observation(observation)
    return observation


def encode_observation(observation: NativeObservation) -> dict[str, Any]:
    """Encode unified facts with tags only for non-JSON semantic values.

    Args:
        observation: Typed facts with valid references and query coverage.

    Returns:
        A JSON-compatible document; unknown and empty fields remain distinct.
    """
    if not isinstance(observation, NativeObservation):
        raise TypeError("Expected NativeObservation")
    validate_query_observation(observation)
    return {"kind": "native_observation", **_encode(observation)}


def encode_read_issue(issue: ReadIssue) -> dict[str, Any]:
    """Encode every field of a retained issue, including empty alternatives and continuation.

    This shares the observation codec's semantic-value tags without traversing the
    unrelated node graph when producing diagnostic pages.
    """
    if not isinstance(issue, ReadIssue):
        raise TypeError("Expected ReadIssue")
    return dict(
        path=list(issue.path),
        state=issue.state,
        detail=issue.detail,
        continuation=_encode(issue.continuation),
        alternatives=_encode(issue.alternatives),
    )
