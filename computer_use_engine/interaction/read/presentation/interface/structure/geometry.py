# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use local bounds to preserve visible line breaks in compact member runs."""

from __future__ import annotations

from collections.abc import Sequence

from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, analyze_alignment
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex


def inline_rows(refs: Sequence[str], geometry: GeometryIndex) -> tuple[tuple[str, ...], ...]:
    """Partition an existing reading order when all members prove distinct visual rows.

    Each row shares the common geometric row criterion. Rows follow the captured
    order. Missing or conflicting geometry keeps
    the original sequence, so this helper never invents or reorders a relationship.

    Args:
        refs: A homogeneous member sequence already approved for compact display.
        geometry: Immutable bounds in their declared comparison spaces.

    Returns:
        Proven visual rows, or one unchanged sequence when bounds are insufficient.
    """
    original = (tuple(refs),)
    boxes = [geometry.boxes.get(ref) for ref in refs]
    if not boxes or any(box is None for box in boxes):
        return original
    measured = tuple(box for box in boxes if box is not None)
    if len({box.space for box in measured}) != 1:
        return original
    if len(set(refs)) != len(refs):
        return original
    analysis = analyze_alignment(tuple(LayoutItem(ref, box) for ref, box in zip(refs, measured)))
    rows = analysis.rows
    if (
        len(rows) < 2
        or any(len(row.members) < 2 for row in rows)
        or tuple(ref for row in rows for ref in row.members) != tuple(refs)
        or any(previous.box.bottom > following.box.y for previous, following in zip(rows, rows[1:]))
    ):
        return original
    return tuple(row.members for row in rows)
