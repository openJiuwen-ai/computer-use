# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve required editing context in the exact captured native text space."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.protocol.observation import NativeNode


def editing_ranges(
    node: NativeNode, path: tuple[str, ...], value: str, projected: Mapping[str, object]
) -> tuple[tuple[int, int], ...] | None:
    """Locate selection and caret context without guessing a different text source.

    Args:
        node: Original native control and its reported text-space metadata.
        path: Native scalar field whose complete content is available in details.
        value: Exact scalar content before presentation shortening.
        projected: Normalized state, used to detect otherwise unaccounted selections.

    Returns:
        Required Python-character ranges, or None when active text cannot be mapped.
        An empty tuple means there is no reported editing range to preserve.
    """
    text = node.text
    semantic = projected.get("selectionRange")
    semantic_selection = isinstance(semantic, Mapping) and semantic.get("length") not in (None, 0)
    if text is None:
        return None if semantic_selection or projected.get("selectedText") else ()
    spans = tuple(text.selections or ())
    if text.caret is not None:
        spans = (*spans, text.caret)
    if not spans:
        return (
            None
            if (text.caret_active or text.selected_text or semantic_selection or projected.get("selectedText"))
            else ()
        )
    if (
        path != ("text", "content")
        and path != text.content_field
        or text.unit not in ("unicode_codepoint", "utf16_code_unit")
    ):
        return None
    offsets = {index: index for index in range(len(value) + 1)}
    if text.unit == "utf16_code_unit":
        offsets, count = {0: 0}, 0
        for index, character in enumerate(value):
            count += len(character.encode("utf-16-le", errors="surrogatepass")) // 2
            offsets[count] = index + 1
    ranges = []
    for span in spans:
        if (
            type(span.start) is not int
            or type(span.end) is not int
            or span.start not in offsets
            or span.end not in offsets
            or span.start > span.end
        ):
            return None
        start, end = offsets[span.start], offsets[span.end]
        if span.text is not None and span.text != value[start:end]:
            return None
        left, right = max(0, start - 80), min(len(value), end + 80)
        if left < right:
            ranges.append((left, right))
    return tuple(ranges)
