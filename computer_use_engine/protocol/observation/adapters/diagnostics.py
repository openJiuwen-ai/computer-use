# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Classify collection metadata while its original query evidence is available."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def catalogue_fields(properties: Sequence[Mapping[str, Any]], row: Mapping[str, Any]) -> frozenset[str]:
    """Locate complete toolkit reflection lists without interpreting provider names.

    The namespace and original property position identify each catalogue. Ordinary
    provider fields with similar names, incomplete lists and parameterized queries
    retain their usual content-preservation rules.
    """
    result = set()
    for index, raw in enumerate(properties):
        if raw["key"] not in (
            ["toolkit", "attribute_names"],
            ["toolkit", "action_names"],
            ["toolkit", "parameterized_attribute_names"],
        ):
            continue
        key = raw["key"]
        name = f"{key[0]}::{key[1]}#{index}"
        value = row["values"].get(name)
        if (
            raw.get("arguments")
            or name in row["fieldStatus"]
            or (raw.get("read") or {}).get("issues")
            or not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
        ):
            continue
        result.add(name)
    return frozenset(result)


def wrapped_status_code(status: Mapping[str, Any], *, has_value: bool) -> bool:
    """Recognize an absent/unsupported result's redundant wrapped native code.

    Exact type and value agreement with the explicit native code establishes the
    diagnostic purpose. Raw results, arbitrary failed values and real read errors
    remain unclassified; their contents may describe information the UI withheld.
    """
    code = status.get("nativeCode")
    return (
        not has_value
        and status.get("state") in ("absent", "unsupported")
        and isinstance(status.get("nativeDomain"), str)
        and bool(status["nativeDomain"])
        and type(code) is int
        and type(status.get("wrappedNativeValue")) is int
        and status["wrappedNativeValue"] == code
    )
