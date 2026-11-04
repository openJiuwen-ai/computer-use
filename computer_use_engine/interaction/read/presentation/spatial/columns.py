# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Project shared geometric anchors into discrete, source-preserving columns."""

from __future__ import annotations

from dataclasses import dataclass, replace

from computer_use.interaction.read.presentation.spatial.alignment import (
    AlignmentAnalysis,
    AlignmentAnchor,
    AlignmentKind,
    anchor_coordinate,
)
from computer_use.interaction.read.presentation.spatial.geometry import Box


@dataclass(frozen=True, slots=True)
class ColumnRow:
    """Original row order and optional column evidence for each independent unit.

    ``None`` keeps a member between its original neighbours without assigning it
    to a column. An entirely unresolved row remains an ordinary visual row.
    """

    members: tuple[str, ...]
    slots: tuple[int | None, ...]


@dataclass(frozen=True, slots=True)
class ColumnProjection:
    """Reliable columns and complete rows, including independently retained floating units."""

    alignments: tuple[AlignmentKind, ...]
    rows: tuple[ColumnRow, ...]


def _select_anchors(analysis: AlignmentAnalysis) -> tuple[AlignmentAnchor, ...]:
    """Choose repeated evidence, retaining an overlapping anchor's unclaimed members."""
    available = frozenset(key for row in analysis.rows for key in row.members)
    selected: list[AlignmentAnchor] = []
    claimed: set[str] = set()
    while True:
        candidates = []
        for anchor in analysis.anchors:
            if not set(anchor.members) <= available:
                continue
            remaining = tuple((key, row) for key, row in zip(anchor.members, anchor.rows) if key not in claimed)
            if len(remaining) < 2 or len({row for _, row in remaining}) != len(remaining):
                continue
            candidates.append(
                replace(anchor, members=tuple(key for key, _ in remaining), rows=tuple(row for _, row in remaining))
            )
        if not candidates:
            break
        # Support is recomputed after each choice. One competing unit cannot
        # erase independent evidence from the other rows of the same anchor.
        chosen = min(
            candidates,
            key=lambda a: (-len(a.members), ("left", "right", "center").index(a.kind), a.position, a.members),
        )
        selected.append(chosen)
        claimed.update(chosen.members)
    return tuple(sorted(selected, key=lambda a: (a.position, a.kind, a.members)))


def _clear_neighbour(
    candidate: Box, witness: Box, anchor: AlignmentAnchor, scale: float, obstacles: tuple[Box, ...]
) -> bool:
    """Require a nearby vertical corridor between two non-overlapping row units."""
    before, after = (candidate, witness) if candidate.y < witness.y else (witness, candidate)
    gap = after.y - before.bottom
    if gap < 0 or gap > 3 * min(scale, candidate.height, witness.height):
        return False
    coordinates = (anchor.position, anchor_coordinate(candidate, anchor.kind), anchor_coordinate(witness, anchor.kind))
    return not any(
        box.y < after.y and box.bottom > before.bottom and box.x < max(coordinates) and box.right > min(coordinates)
        for box in obstacles
    )


def _refine_floating(
    analysis: AlignmentAnalysis,
    anchors: tuple[AlignmentAnchor, ...],
    rows: tuple[ColumnRow, ...],
    assigned: frozenset[str],
    conflicting_rows: frozenset[int],
) -> tuple[ColumnRow, ...]:
    """Attach unique near misses once, using only established neighbouring evidence.

    Every proposed assignment is evaluated against the same input rows. Newly
    assigned members never support another assignment, and competing proposals
    stay independent. Original anchor choices and conflicting rows remain intact.
    """
    boxes = {item.key: item.box for item in analysis.items}
    if (
        set(boxes) != {key for row in rows for key in row.members}
        or analysis.scale <= 0
        or len({box.space for box in boxes.values()}) != 1
    ):
        return rows
    result = []
    for row_index, row in enumerate(rows):
        if row_index in conflicting_rows:
            result.append(row)
            continue
        neighbours = tuple(
            (column, key)
            for index in (row_index - 1, row_index + 1)
            if 0 <= index < len(rows)
            for key, column in zip(rows[index].members, rows[index].slots)
            if column is not None
        )
        proposals: dict[int, int] = {}
        for member_index, (key, current) in enumerate(zip(row.members, row.slots)):
            if current is not None or key in assigned:
                continue
            box = boxes[key]
            candidates: set[int] = set()
            for column, witness_key in neighbours:
                if column in row.slots:
                    continue
                if any(
                    slot is not None and (slot >= column if index < member_index else slot <= column)
                    for index, slot in enumerate(row.slots)
                ):
                    continue
                anchor, witness = anchors[column], boxes[witness_key]
                tolerance = 0.4 * min(analysis.scale, box.height, witness.height)
                if abs(anchor_coordinate(box, anchor.kind) - anchor.position) > tolerance:
                    continue
                # A unit spanning another supported column is not an ordinary
                # missing cell, even when one of its edges nearly aligns.
                if any(
                    other_column != column
                    and min(box.right, boxes[other_key].right) - max(box.x, boxes[other_key].x)
                    > 0.35 * min(box.width, boxes[other_key].width)
                    for other_column, other_key in neighbours
                ):
                    continue
                obstacles = tuple(value for other_key, value in boxes.items() if other_key not in (key, witness_key))
                if _clear_neighbour(box, witness, anchor, analysis.scale, obstacles):
                    candidates.add(column)
            if len(candidates) == 1:
                proposals[member_index] = next(iter(candidates))
        # Two independent units may both fit one empty slot, or propose reversed
        # columns. Retain both as floating rather than choosing by traversal order.
        conflicts = {
            index
            for index, column in proposals.items()
            for other, target in proposals.items()
            if index != other and (column == target or (index < other) != (column < target))
        }
        result.append(
            ColumnRow(
                row.members,
                tuple(
                    proposals[index] if index in proposals and index not in conflicts else slot
                    for index, slot in enumerate(row.slots)
                ),
            )
        )
    return tuple(result)


def project_columns(analysis: AlignmentAnalysis) -> ColumnProjection | None:
    """Choose compatible repeated anchors and retain sparse rows without padding facts.

    Args:
        analysis: Complete local row bands and their cross-row alignment candidates.

    Returns:
        Supported columns and every original member in its unchanged row order.
        A near miss may use one unambiguous neighbouring column; other unanchored
        units carry no column. A conflicting row loses only its own assignments.
        Fewer than two reliable columns and two multi-column rows provide
        insufficient repeated structure and return None.
    """
    if len(analysis.rows) < 2:
        return None
    selected = _select_anchors(analysis)
    if len(selected) < 2:
        return None
    column_for = {key: index for index, anchor in enumerate(selected) for key in anchor.members}
    rows = []
    conflicting_rows: set[int] = set()
    for index, row in enumerate(analysis.rows):
        slots = tuple(column_for.get(key) for key in row.members)
        known = tuple(slot for slot in slots if slot is not None)
        if tuple(sorted(set(known))) != known:
            # Preserve the visible order and content when alignment candidates
            # disagree with this row, leaving other rows' evidence usable.
            slots = (None,) * len(row.members)
            conflicting_rows.add(index)
        rows.append(ColumnRow(row.members, slots))
    support = [sum(column in row.slots for row in rows) for column in range(len(selected))]
    retained = tuple(column for column, count in enumerate(support) if count >= 2)
    if len(retained) < 2:
        return None
    remap = {old: new for new, old in enumerate(retained)}
    projected = tuple(ColumnRow(row.members, tuple(remap.get(slot) for slot in row.slots)) for row in rows)
    if sum(sum(slot is not None for slot in row.slots) > 1 for row in projected) < 2:
        return None
    anchors = tuple(selected[column] for column in retained)
    projected = _refine_floating(analysis, anchors, projected, frozenset(column_for), frozenset(conflicting_rows))
    return ColumnProjection(tuple(anchor.kind for anchor in anchors), projected)
