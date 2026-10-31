# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize discrete columns with bounded Unicode-aware alignment whitespace."""

from __future__ import annotations

from collections.abc import Sequence
import unicodedata


def floating_row(cells: Sequence[tuple[int | None, str]], column_count: int) -> str:
    """Render an irregular row while keeping independent items in source order.

    Args:
        cells: Prepared logical slots and complete cell text. None identifies an
            item with no column assignment, retained between its observed neighbours.
        column_count: Number of established columns in the containing layout.

    Returns:
        A compact row with column separators for known slots and semicolons for
        independent items. Missing slots stay empty; no placeholder facts are added.
    """
    parts: list[str] = []
    previous = -1
    for slot, text in cells:
        if slot is None:
            parts.append((" ; " if parts else "") + text)
        else:
            separator = (" ; " if parts else "") + " | " * slot if previous < 0 else " | " * (slot - previous)
            parts.append(separator + text)
            previous = slot
    if previous >= 0:
        parts.append(" | " * (column_count - previous - 1))
    return "".join(parts)


def _width(text: str) -> int:
    """Measure ordinary CJK/Latin display cells; complex joined glyphs skip padding."""
    return sum(
        0
        if unicodedata.combining(character) or unicodedata.category(character) == "Cf"
        else 2
        if unicodedata.east_asian_width(character) in ("W", "F")
        else 1
        for character in text
    )


def compact_grid(rows: Sequence[Sequence[str]], alignments: Sequence[str]) -> tuple[str, ...]:
    """Render preselected slots without interpreting their contents or coordinates.

    Args:
        rows: Complete text cells in an already chosen rectangular display matrix.
            Empty slots carry no source fact and remain visibly empty between separators.
        alignments: Left, right or center alignment established by the layout plan.

    Returns:
        One compact line per row. A column aligns only when at most four added spaces
        per cell suffice; a long value never stretches the rest of the display.
    """
    if not rows:
        return ()
    count = len(alignments)
    if any(len(row) != count for row in rows) or any(kind not in ("left", "right", "center") for kind in alignments):
        raise ValueError("Compact columns differ from their selected layout")
    widths = [tuple(_width(cell) for cell in row) for row in rows]
    maxima = tuple(max(row[column] for row in widths) for column in range(count))
    padding = tuple(
        maxima[column] - min(row[column] for row in widths) <= 4 and all("\u200d" not in row[column] for row in rows)
        for column in range(count)
    )
    result = []
    for row, sizes in zip(rows, widths):
        cells = []
        for index, (text, size, kind) in enumerate(zip(row, sizes, alignments)):
            extra = maxima[index] - size if padding[index] else 0
            left = extra if kind == "right" else extra // 2 if kind == "center" else 0
            right = extra - left if index < count - 1 else 0
            cells.append(" " * left + text + " " * right)
        result.append(" | ".join(cells))
    return tuple(result)
