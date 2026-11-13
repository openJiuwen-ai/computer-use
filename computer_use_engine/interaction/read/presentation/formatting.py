# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""String/number formatting at the existing model-observation boundary."""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import TYPE_CHECKING

import regex

from computer_use.protocol.primitives import trim, utf16_length

if TYPE_CHECKING:
    from typing import Any

    from computer_use.protocol.records import Record


def json_string(value: Any) -> str:
    if re.search(r"[\ud800-\udfff]", value):
        value = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return re.sub(r"[\ud800-\udfff]", lambda m: f"\\u{ord(m[0]):04x}", encoded)


def number_string(value: Any) -> str:
    """Format numeric values consistently for model-facing observation text."""
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is int:
        return str(value)
    if value == 0:
        return "0"
    if not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, int):
        value = float(value)
    text = repr(value)
    if 1e-6 <= abs(value) < 1e21:
        return (
            format(Decimal(text), "f").rstrip("0").rstrip(".")
            if "." in format(Decimal(text), "f")
            else format(Decimal(text), "f")
        )
    mantissa, exponent = text.lower().split("e") if "e" in text.lower() else (text, "0")
    mantissa = mantissa.removesuffix(".0")
    number = int(exponent)
    return mantissa + "e" + ("+" if number >= 0 else "-") + str(abs(number))


def shortened(value: str, limit: float) -> Record:
    """Build a head/tail text preview at grapheme boundaries.

    Args:
        value: Original string to preview.
        limit: Preview threshold; short strings are measured in UTF-16 units before
            the grapheme-based head/tail split.

    Returns:
        Head text, tail text, and the count omitted between them.
    """
    if utf16_length(value) <= limit:
        return dict(head=value, tail="", omitted=0)
    head_limit, tail_limit = math.floor(limit / 4), limit - math.floor(limit / 4)
    head_value, tail_value, count_value, tail_index = [], [], 0, 0
    if re.search("[\\ud800-\\udfff]", value):
        value = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    for match in regex.finditer("\\X", value):
        segment = match[0]
        if count_value < head_limit:
            head_value.append(segment)
        else:
            if tail_index < len(tail_value):
                tail_value[tail_index] = segment
            else:
                tail_value.append(segment)
            tail_index = (tail_index + 1) % tail_limit
        count_value += 1
    if count_value <= limit:
        return dict(head=value, tail="", omitted=0)
    return dict(
        head="".join(head_value),
        tail="".join(tail_value[tail_index:] + tail_value[:tail_index]),
        omitted=count_value - limit,
    )


def quoted(text: str) -> str:
    if text is None:
        return "null"
    return re.sub("[\\x7f-\\x9f\\u2028\\u2029]", lambda m: f"\\u{ord(m[0]):04x}", json_string(text))


def field(text: str) -> str:
    return (
        quoted(text)
        if (
            not text
            or text != trim(text)
            or re.search('[,">\\\\\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]', text)
            or re.match("[<+~|]", text)
            or re.search("\\(partial:|\\(showing ", text)
        )
        else text
    )


def bounded(text: str, limit: int) -> str:
    part = shortened(text, limit)
    return f"{part['head']}\n[… {part['omitted']} characters omitted …]\n{part['tail']}" if part["omitted"] else text
