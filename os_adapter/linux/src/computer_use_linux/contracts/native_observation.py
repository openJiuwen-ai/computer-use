# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lossless NativeObservation wire primitives for the independent Linux driver.

The driver has no dependency on the public SDK. These constructors implement
the shared graph wire contract; collection records remain local to a request.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from typing import Any


def value(data: Any, native_type: str | None = None, *, metadata: list[list[Any]] | None = None) -> dict[str, Any]:
    """Encode a collected value without changing scalar content or ordering.

    Args:
        data: A scalar or collection returned by the acquisition adapter.
        native_type: An API-confirmed native type, when known.
        metadata: Typed qualifiers such as coordinate space and units.

    Returns:
        A Value record accepted by the shared NativeObservation codec.
    """
    if data is None:
        encoded, inferred = None, "python.NoneType"
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    elif type(data) is bool:
        encoded, inferred = data, "python.bool"
    # pylint: disable-next=unidiomatic-typecheck
    elif type(data) is int:
        encoded = data if abs(data) <= 9007199254740991 else {"integer": str(data)}
        inferred = "python.int"
    # pylint: disable-next=unidiomatic-typecheck
    elif type(data) is float:
        if math.isnan(data):
            encoded = {"float": "nan"}
        elif math.isinf(data):
            encoded = {"float": "inf" if data > 0 else "-inf"}
        elif data == 0 and math.copysign(1, data) < 0:
            encoded = {"float": "-0"}
        else:
            encoded = data
        inferred = "python.float"
    elif isinstance(data, str):
        encoded, inferred = data, "python.str"
    elif isinstance(data, bytes):
        encoded = {"bytes": base64.b64encode(data).decode("ascii")}
        inferred = "python.bytes"
    elif isinstance(data, Mapping):
        if all(key is None or type(key) in (str, int) for key in data):
            encoded = {"fields": [[key, value(item)] for key, item in data.items()]}
            inferred = "toolkit.record"
        else:
            encoded = {"entries": [[value(key), value(item)] for key, item in data.items()]}
            inferred = "python.dict"
    elif isinstance(data, (list, tuple)):
        encoded = {"items": [value(item) for item in data]}
        inferred = "python.tuple" if isinstance(data, tuple) else "python.list"
    else:
        raise TypeError("Unsupported collected native value: " + type(data).__name__)
    result = dict(nativeType=native_type or inferred, data=encoded)
    if metadata:
        result["metadata"] = metadata
    return result


def details(**items: Any) -> list[list[Any]]:
    """Encode ordered acquisition qualifiers without discarding false values."""
    return [[key, value(item)] for key, item in items.items()]


def reference(node: str) -> dict[str, Any]:
    """Encode one retained AT-SPI object reference, including duplicate edges."""
    return dict(nativeType="AtspiAccessible", data=dict(ref=node))


def struct(data: Mapping[str, Any], native_type: str, *, metadata: list[list[Any]] | None = None) -> dict[str, Any]:
    """Encode an API-defined record in its original field order."""
    return value(data, native_type, metadata=metadata)


def diagnostic_result(data: Any) -> dict[str, Any]:
    """Retain a rejected native payload, marking unencodable slots explicitly.

    Collection errors may contain values outside the public semantic field
    schema. This bounded conversion preserves encodable content without making
    those values successful field results or invoking native object methods.
    """
    remaining = [32768]

    def issue(item: Any, path: tuple[str | int, ...], reason: str) -> dict[str, Any]:
        native_type = type(item).__module__ + "." + type(item).__qualname__
        return dict(
            issue=dict(
                reason=reason,
                path=list(path),
                detail="Rejected native result slot cannot be encoded as a collected value.",
                nativeStatus=value(native_type, "python.type"),
            )
        )

    def encode(item: Any, path: tuple[str | int, ...], ancestors: set[int]) -> dict[str, Any]:
        remaining[0] -= 1
        if remaining[0] < 0 or len(path) > 32:
            return issue(item, path, "native_result_budget_exhausted")
        if not isinstance(item, (Mapping, list, tuple)):
            try:
                return value(item)
            except (TypeError, ValueError, OverflowError):
                return issue(item, path, "unencodable_native_result")
        if id(item) in ancestors:
            return issue(item, path, "native_result_value_cycle")
        nested = ancestors | {id(item)}
        if isinstance(item, (list, tuple)):
            children = []
            for index, child in enumerate(item):
                children.append(encode(child, (*path, index), nested))
                if remaining[0] < 0:
                    break
            result = dict(
                nativeType="python.tuple" if isinstance(item, tuple) else "python.list", data=dict(items=children)
            )
            if len(children) < len(item):
                result["metadata"] = details(original_length=len(item), complete=False)
            return result
        if all(key is None or type(key) in (str, int) for key in item):
            children = []
            for index, (key, child) in enumerate(item.items()):
                children.append([key, encode(child, (*path, key if key is not None else index), nested)])
                if remaining[0] < 0:
                    break
            result = dict(nativeType="python.dict", data=dict(fields=children))
            if len(children) < len(item):
                result["metadata"] = details(original_length=len(item), complete=False)
            return result
        entries = []
        for index, (key, child) in enumerate(item.items()):
            try:
                encoded_key = value(key)
            except (TypeError, ValueError, OverflowError, RecursionError):
                return issue(item, path, "unencodable_native_map_key")
            entries.append([encoded_key, encode(child, (*path, index), nested)])
        return dict(nativeType="python.dict", data=dict(entries=entries))

    return encode(data, (), set())
