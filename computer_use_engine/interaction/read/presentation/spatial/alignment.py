# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discover visual row bands and repeated alignment anchors from immutable boxes.

The anchor model follows LiteParse's separation of left, right and centre
alignment. UI units retain their identities and complete rectangles; callers
decide which candidates become fields, records or independent regions.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Literal

from computer_use.interaction.read.presentation.spatial.geometry import Box

AlignmentKind = Literal["left", "right", "center"]


@dataclass(frozen=True, slots=True)
class LayoutItem:
    """One source-preserving visual unit in an already selected layout scope.

    Attributes:
        key: Existing unit identity, independent of its position and caption.
        box: Complete comparable bounds; their coordinate space stays unchanged.
    """

    key: str
    box: Box


@dataclass(frozen=True, slots=True)
class RowBand:
    """A candidate visual row, ordered horizontally with all its source units.

    Attributes:
        members: Unit identities in left-to-right geometric order.
        box: Union of the unchanged member rectangles.
    """

    members: tuple[str, ...]
    box: Box


@dataclass(frozen=True, slots=True)
class AlignmentAnchor:
    """An unobstructed alignment repeated across separate visual rows.

    Attributes:
        kind: The edge or centre measured for every participating unit.
        position: Median physical coordinate of the participating measurements.
        members: Original unit identities, ordered by their visual rows.
        rows: Indices into the analysis's row bands, paired with ``members``.
    """

    kind: AlignmentKind
    position: float
    members: tuple[str, ...]
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AlignmentAnalysis:
    """Complete candidate rows and repeated anchors for one coordinate space.

    Attributes:
        rows: Every input unit appears exactly once, including isolated units.
        anchors: Optional cross-row evidence; several kinds may describe a unit.
        scale: Median unit height used to normalize geometric tolerances.
        items: Original units in deterministic row order, retained for local
            column refinement. Empty when only row and anchor facts are available.
    """

    rows: tuple[RowBand, ...]
    anchors: tuple[AlignmentAnchor, ...]
    scale: float
    items: tuple[LayoutItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


def _union(items: Sequence[LayoutItem]) -> Box:
    """Measure a complete group without changing any member rectangle."""
    left = min(item.box.x for item in items)
    top = min(item.box.y for item in items)
    return Box(
        items[0].box.space,
        left,
        top,
        max(item.box.right for item in items) - left,
        max(item.box.bottom for item in items) - top,
    )


def _horizontal(items: Sequence[LayoutItem]) -> tuple[LayoutItem, ...]:
    return tuple(sorted(items, key=lambda item: (item.box.x, item.box.center[0], item.key)))


def _row_compatible(items: Sequence[LayoutItem]) -> bool:
    """Require a shared band and distinct horizontal units across the whole row."""
    heights = tuple(item.box.height for item in items)
    smallest = min(heights)
    if max(heights) > 3 * smallest:
        return False
    top = max(item.box.y for item in items)
    bottom = min(item.box.bottom for item in items)
    centers = tuple(item.box.center[1] for item in items)
    if bottom - top < 0.6 * smallest or max(centers) - min(centers) > 0.5 * smallest:
        return False
    ordered = _horizontal(items)
    for left, right in zip(ordered, ordered[1:]):
        first, second = left.box, right.box
        width = min(first.width, second.width)
        if (
            second.x <= first.x
            or second.center[0] <= first.center[0]
            or first.contains(second)
            or second.contains(first)
            or first.right - second.x > 0.35 * width
            or second.center[0] - first.center[0] < 0.5 * width
        ):
            return False
    return True


def same_row(items: Sequence[LayoutItem], *, require_order: bool = False) -> bool:
    """Check a complete candidate row using the shared geometric criteria.

    Args:
        items: Units to compare within one coordinate space.
        require_order: Require the supplied sequence to follow horizontal order.

    Returns:
        Whether all units form one compatible visual row. Empty, repeated and
        incomparable units cannot establish a row. No anchors are recomputed.
    """
    if not items or len({item.key for item in items}) != len(items) or len({item.box.space for item in items}) != 1:
        return False
    return _row_compatible(items) and (not require_order or tuple(items) == _horizontal(items))


def shared_anchors(analysis: AlignmentAnalysis, keys: Sequence[str]) -> tuple[AlignmentAnchor, ...]:
    """Find repeated left, right or centre evidence covering every requested unit.

    Args:
        analysis: Candidate rows and anchors from the complete local scope.
        keys: Distinct unit identities whose common alignment is being tested.

    Returns:
        Existing anchors containing all units. At least two identities are
        required; missing or repeated identities provide no alignment evidence.
    """
    selected = frozenset(keys)
    if len(selected) < 2 or len(selected) != len(keys):
        return ()
    return tuple(anchor for anchor in analysis.anchors if selected.issubset(anchor.members))


def _form_rows(items: Sequence[LayoutItem]) -> tuple[RowBand, ...]:
    """Cluster complete row bands with a deterministic geometric insertion order."""
    groups: list[list[LayoutItem]] = []
    ordered = sorted(items, key=lambda item: (item.box.center[1], item.box.y, item.box.x, item.key))
    for item in ordered:
        candidates = [index for index, group in enumerate(groups) if _row_compatible((*group, item))]
        if candidates:
            # Whole-band compatibility bounds drift; the nearest row centre
            # chooses between two independently compatible candidate bands.
            selected = min(
                candidates,
                key=lambda index: (
                    abs(item.box.center[1] - median(member.box.center[1] for member in groups[index])),
                    tuple(member.key for member in _horizontal(groups[index])),
                ),
            )
            groups[selected].append(item)
        else:
            groups.append([item])
    bands = [RowBand(tuple(item.key for item in _horizontal(group)), _union(group)) for group in groups]
    return tuple(sorted(bands, key=lambda row: (row.box.center[1], row.box.y, row.box.x, row.members)))


def anchor_coordinate(box: Box, kind: AlignmentKind) -> float:
    """Measure the original edge or centre used by an alignment candidate."""
    if kind == "left":
        return box.x
    if kind == "right":
        return box.right
    return box.center[0]


def _anchor_groups(
    items: Sequence[LayoutItem], kind: AlignmentKind, scale: float
) -> tuple[tuple[LayoutItem, ...], ...]:
    """Cluster one anchor kind using bounded diameter instead of neighbour chaining."""
    groups: list[list[LayoutItem]] = []
    for item in sorted(items, key=lambda member: (anchor_coordinate(member.box, kind), member.box.y, member.key)):
        if groups:
            group = groups[-1]
            tolerance = 0.25 * min(scale, item.box.height, *(member.box.height for member in group))
            if anchor_coordinate(item.box, kind) - anchor_coordinate(group[0].box, kind) <= tolerance:
                group.append(item)
                continue
        groups.append([item])
    return tuple(tuple(group) for group in groups)


def _vertical_intervals(
    items: Sequence[LayoutItem], position: float, supporting_rows: frozenset[int], row_of: dict[str, int]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Index intervening units crossing an anchor's physical vertical line."""
    intervals = sorted(
        (item.box.y, item.box.bottom)
        for item in items
        if row_of[item.key] not in supporting_rows and item.box.x < position < item.box.right
    )
    starts: list[float] = []
    ends: list[float] = []
    highest = float("-inf")
    for top, bottom in intervals:
        starts.append(top)
        highest = max(highest, bottom)
        ends.append(highest)
    return tuple(starts), tuple(ends)


def _blocked(before: LayoutItem, after: LayoutItem, starts: tuple[float, ...], ends: tuple[float, ...]) -> bool:
    """Test a real inter-row corridor against the precomputed obstruction index."""
    top, bottom = before.box.bottom, after.box.y
    if bottom <= top:
        return False
    index = bisect_left(starts, bottom) - 1
    return index >= 0 and ends[index] > top


def _unambiguous_members(group: Sequence[LayoutItem], row_of: dict[str, int]) -> list[LayoutItem]:
    """Keep one measurement per row and exclude overlapping alternative objects."""
    counts = Counter(row_of[item.key] for item in group)
    candidates = [item for item in group if counts[row_of[item.key]] == 1]
    active: list[LayoutItem] = []
    conflicting: set[str] = set()
    for item in sorted(candidates, key=lambda member: (member.box.y, member.key)):
        active = [previous for previous in active if previous.box.bottom > item.box.y]
        for previous in active:
            overlap = min(previous.box.bottom, item.box.bottom) - item.box.y
            if overlap > 0.25 * min(previous.box.height, item.box.height):
                conflicting.update((previous.key, item.key))
        active.append(item)
    return sorted(
        (item for item in candidates if item.key not in conflicting),
        key=lambda item: (row_of[item.key], item.box.y, item.key),
    )


def _anchors(items: Sequence[LayoutItem], rows: tuple[RowBand, ...], scale: float) -> tuple[AlignmentAnchor, ...]:
    """Retain supported local runs while leaving obstructed and isolated units intact."""
    row_of = {key: index for index, row in enumerate(rows) for key in row.members}
    result: list[AlignmentAnchor] = []
    for kind in ("left", "right", "center"):
        for group in _anchor_groups(items, kind, scale):
            # Two measurements in one row leave that row's column membership
            # ambiguous. Other rows can still establish their own anchor.
            members = _unambiguous_members(group, row_of)
            if len(members) < 2:
                continue
            position = median(anchor_coordinate(item.box, kind) for item in members)
            starts, ends = _vertical_intervals(items, position, frozenset(row_of[item.key] for item in members), row_of)
            run: list[LayoutItem] = []

            def flush() -> None:
                if len(run) >= 2:
                    result.append(
                        AlignmentAnchor(
                            kind,
                            median(anchor_coordinate(item.box, kind) for item in run),
                            tuple(item.key for item in run),
                            tuple(row_of[item.key] for item in run),
                        )
                    )
                run.clear()

            for item in members:
                if run:
                    previous = run[-1]
                    overlap = min(previous.box.bottom, item.box.bottom) - max(previous.box.y, item.box.y)
                    if overlap > 0.25 * min(previous.box.height, item.box.height) or _blocked(
                        previous, item, starts, ends
                    ):
                        flush()
                run.append(item)
            flush()
    return tuple(result)


def analyze_alignment(items: Sequence[LayoutItem]) -> AlignmentAnalysis:
    """Discover row and alignment candidates without changing source membership.

    Args:
        items: Unique visual units whose complete boxes share one coordinate
            space. The caller establishes window and semantic boundaries.

    Returns:
        Deterministic row bands covering all units, cross-row left/right/centre
        anchors and a local height scale. Candidates remain independent of roles
        and input enumeration order, and preserve translation/scaling relations.

    Raises:
        ValueError: Unit identities repeat or boxes use different spaces.
    """
    if len({item.key for item in items}) != len(items):
        raise ValueError("Geometric alignment requires unique unit identities")
    if len({item.box.space for item in items}) > 1:
        raise ValueError("Geometric alignment requires one coordinate space")
    if not items:
        return AlignmentAnalysis((), (), 0.0)
    scale = median(item.box.height for item in items)
    rows = _form_rows(items)
    by_key = {item.key: item for item in items}
    original = tuple(by_key[key] for row in rows for key in row.members)
    return AlignmentAnalysis(rows, _anchors(items, rows, scale), scale, original)
