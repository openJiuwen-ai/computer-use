# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Equivalence evidence for multiple views of one captured scalar."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from computer_use.protocol.observation import FieldPath


def numeric_aliases(
    source: Mapping[str, Any], primary: str | None, selectors: Mapping[tuple[str, FieldPath], str]
) -> tuple[str, ...]:
    """Find complete numeric fields derived from the primary field's exact getter.

    Equal text or equal numbers from independent getters do not establish this
    relationship. Types and signed zero are preserved before formatting values.
    """
    values, paths, statuses = source["values"], source["fieldPaths"], source["fieldStatus"]
    value = values.get(primary)
    if type(value) not in (int, float) or primary in statuses or type(value) is float and not math.isfinite(value):
        return ()
    selector = selectors.get((source["node"], paths[primary]))
    if not selector:
        return ()
    result = []
    for name in ("control_value.value", "numeric_value.value"):
        other = values.get(name)
        if (
            name == primary
            or name in statuses
            or type(other) is not type(value)
            or other != value
            or selectors.get((source["node"], paths[name])) != selector
        ):
            continue
        if type(value) is float and value == 0 and math.copysign(1, value) != math.copysign(1, other):
            continue
        result.append(name)
    return tuple(result)
