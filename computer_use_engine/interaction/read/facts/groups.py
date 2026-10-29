# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lossless presentation of properties with identical values and reference sets.

Each output record is a Cartesian product: every named property has exactly the
displayed value/status on every listed reference. Overlapping sets are not equal
sets. This formatter neither folds nodes nor changes the native fact graph.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def reference_set(values: Any) -> str:
    """Lossless shorthand for numeric display aliases, not tree order."""
    values = sorted({int(value) for value in values})
    parts, index = [], 0
    while index < len(values):
        end = index
        while end + 1 < len(values) and values[end + 1] == values[end] + 1:
            end += 1
        if end - index >= 2:
            parts.append(f"{values[index]}–{values[end]}")
        else:
            parts.extend(str(value) for value in values[slice(index, end + 1)])
        index = end + 1
    return ", ".join(parts)


def render_properties(groups: Mapping[tuple[str, str], list[str]]) -> list[str]:
    """Format (property, already-encoded value/status) → observed references."""
    shared = {}
    for (property_name, value_value), refs_value in groups.items():
        key = (frozenset(refs_value), value_value)
        shared.setdefault(key, []).append(property_name)
    lines = []
    for (refs_value, value_value), properties in shared.items():
        if not refs_value:
            continue
        if len(properties) == 1:
            property_name = properties[0]
            name = (
                property_name
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", property_name)
                else json.dumps(property_name, ensure_ascii=False)
            )
        else:
            name = "Properties " + json.dumps(properties, ensure_ascii=False, separators=(",", ":"))
        lines.append(reference_set(refs_value) + " " + name + ": " + value_value)
    return lines


if TYPE_CHECKING:
    from typing import Mapping
