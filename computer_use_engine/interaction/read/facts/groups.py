# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Format numbered element ranges in observation notices."""

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
