# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose a reversible scalar spelling for a complete multiline value."""

from __future__ import annotations

from collections.abc import Callable
import re


_MINIMUM_SAVING = 8


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8", errors="surrogatepass"))


def compact_multiline_value(
    value: str,
    *,
    label: str,
    indent: str,
    inline_prefix: str,
    quote: Callable[[str], str],
    format_field: Callable[[str], str],
) -> str | None:
    """Prefer JSON only when it is smaller than the complete paragraph block.

    Args:
        value: Complete captured string, with its original paragraph separators.
        label: Existing block label, such as ``Value`` or ``Document text``.
        indent: Existing node indentation.
        inline_prefix: Separator and optional label cost added to the node line;
            callers may use an upper bound before assembling later fields.
        quote: Existing JSON scalar formatter, including its Unicode escaping rules.
        format_field: Existing formatter used for each paragraph in the block.

    Returns:
        The complete quoted scalar when it saves at least eight UTF-8 bytes;
        otherwise None, leaving the existing block representation in use.
    """
    if not any(character in value for character in "\r\n"):
        return None
    scalar = quote(value)
    block = (
        "\n"
        + indent
        + "\t"
        + label
        + ":\n"
        + "\n".join(indent + "\t| " + format_field(paragraph) for paragraph in re.split(r"\r\n|\r|\n", value))
    )
    return scalar if _utf8_size(block) - _utf8_size(inline_prefix + scalar) >= _MINIMUM_SAVING else None
