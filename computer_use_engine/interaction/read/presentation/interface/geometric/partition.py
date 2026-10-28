# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Partition observed units into nested reading regions using local whitespace."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import heapq
from statistics import median
from typing import Literal

from computer_use.interaction.read.presentation.spatial.alignment import (
    AlignmentAnalysis,
    LayoutItem,
    analyze_alignment,
)

Axis = Literal["horizontal", "vertical"]


@dataclass(frozen=True, slots=True)
class RegionPlan:
    """A recursive partition whose leaves retain all original unit keys.

    Attributes:
        keys: Complete member sequence in this region's reading order.
        axis: Spatial axis separating the children, or None for an indivisible leaf.
        children: Non-overlapping regions in constraint-compatible reading order.
    """

    keys: tuple[str, ...]
    axis: Axis | None = None
    children: tuple[RegionPlan, ...] = ()


@dataclass(frozen=True, slots=True)
class RegionCandidate:
    """One complete local partition hypothesis before any blocks are changed.

    Attributes:
        plan: Complete source membership under a single proposed spatial split.
        score: Normalized separation evidence after relationship checks.
        evidence: Geometric and explicit-pane evidence supporting this split.
    """

    plan: RegionPlan
    score: float
    evidence: tuple[str, ...] = ()


def leaf_paths(forest: Sequence[RegionPlan]) -> dict[str, tuple[int, ...]]:
    """Index every unit's complete region path without flattening horizontal splits.

    Args:
        forest: Independent ordered segment trees, including unpositioned leaves.

    Returns:
        One path per source unit, starting with its forest-root index. The paths
        describe internal boundaries; callers decide which regions need headings.

    Raises:
        ValueError: A unit occurs in several leaves or a split changes membership.
    """
    paths: dict[str, tuple[int, ...]] = {}
    pending = [(region, (index,)) for index, region in reversed(tuple(enumerate(forest)))]
    while pending:
        region, path = pending.pop()
        if region.children:
            if region.keys != tuple(key for child in region.children for key in child.keys):
                raise ValueError("Geometric region children changed their parent membership")
            pending.extend((child, (*path, index)) for index, child in reversed(tuple(enumerate(region.children))))
        else:
            for key in region.keys:
                if key in paths:
                    raise ValueError("Geometric region unit occurs in multiple leaves")
                paths[key] = path
    return paths


@dataclass(frozen=True, slots=True)
class _Gap:
    axis: Axis
    start: float
    stop: float

    @property
    def width(self) -> float:
        return self.stop - self.start


@dataclass(frozen=True, slots=True)
class _Cut:
    gap: _Gap
    first: tuple[str, ...]
    second: tuple[str, ...]
    score: float


def _gaps(items: Sequence[LayoutItem], axis: Axis) -> tuple[_Gap, ...]:
    """Find empty projection intervals without slicing an observed unit."""
    intervals = sorted(
        (item.box.x, item.box.right) if axis == "vertical" else (item.box.y, item.box.bottom) for item in items
    )
    if not intervals:
        return ()
    end = intervals[0][1]
    result = []
    for start, stop in intervals[1:]:
        if start > end:
            result.append(_Gap(axis, end, start))
        end = max(end, stop)
    return tuple(result)


def _stable_order(keys: Sequence[str], before: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """Apply explicit precedence with the geometric sequence as the tie breaker."""
    positions = {key: index for index, key in enumerate(keys)}
    incoming = dict.fromkeys(keys, 0)
    outgoing: dict[str, set[str]] = {key: set() for key in keys}
    for first, second in before:
        if first == second or first not in positions or second not in positions or second in outgoing[first]:
            continue
        outgoing[first].add(second)
        incoming[second] += 1
    ready = [(positions[key], key) for key in keys if incoming[key] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, key = heapq.heappop(ready)
        result.append(key)
        for target in outgoing[key]:
            incoming[target] -= 1
            if incoming[target] == 0:
                heapq.heappush(ready, (positions[target], target))
    if len(result) != len(keys):
        raise ValueError("Geometric partition precedence contains a cycle")
    return tuple(result)


def _row_sets(keys: frozenset[str], alignment: AlignmentAnalysis) -> tuple[frozenset[str], ...]:
    return tuple(members for row in alignment.rows if (members := keys.intersection(row.members)))


def _independent_groups(
    first: frozenset[str], second: frozenset[str], groups: tuple[frozenset[str], ...], alignment: AlignmentAnalysis
) -> bool:
    """Recognize explicitly grouped vertical runs on both sides of a gutter."""
    for side in (first, second):
        rows = _row_sets(side, alignment)
        if not any(
            group <= side and len(group) >= 2 and sum(bool(group & row) for row in rows) >= max(2, 0.6 * len(rows))
            for group in groups
        ):
            return False
    return True


def _columns_supported(
    first: tuple[str, ...],
    second: tuple[str, ...],
    items: dict[str, LayoutItem],
    groups: tuple[frozenset[str], ...],
    alignment: AlignmentAnalysis,
    independent: frozenset[str] = frozenset(),
) -> bool:
    """Require independent vertical content, protecting repeated cross-column rows.

    Matching rows are sufficient to retain a possible form or table as one region.
    An explicit vertical grouping on each side can establish independent panes
    even when their baselines happen to match.
    """
    left, right = frozenset(first), frozenset(second)
    rows_left, rows_right = _row_sets(left, alignment), _row_sets(right, alignment)
    first_pane = len(first) == 1 and first[0] in independent
    second_pane = len(second) == 1 and second[0] in independent
    if len(rows_left) < 2 and not first_pane or len(rows_right) < 2 and not second_pane:
        return False
    first_band = min(items[key].box.y for key in first), max(items[key].box.bottom for key in first)
    second_band = min(items[key].box.y for key in second), max(items[key].box.bottom for key in second)
    overlap = min(first_band[1], second_band[1]) - max(first_band[0], second_band[0])
    if overlap < 0.6 * min(first_band[1] - first_band[0], second_band[1] - second_band[0]):
        return False
    paired = sum(
        bool(left.intersection(row.members)) and bool(right.intersection(row.members)) for row in alignment.rows
    )
    if paired >= 2 and paired >= 0.6 * min(len(rows_left), len(rows_right)):
        return _independent_groups(left, right, groups, alignment)
    return True


def _spanning_boundary(
    first: tuple[str, ...],
    second: tuple[str, ...],
    items: dict[str, LayoutItem],
    scale: float,
    independent: frozenset[str] = frozenset(),
) -> bool:
    """Find a horizontal break separating a spanning item from local columns."""
    for spanning, body in ((first, second), (second, first)):
        if len(body) < 4 and not (len(body) >= 3 and independent.intersection(body)):
            continue
        for gutter in _gaps(tuple(items[key] for key in body), "vertical"):
            left = tuple(key for key in body if items[key].box.right <= gutter.start)
            right = tuple(key for key in body if items[key].box.x >= gutter.stop)
            pane_side = len(left) == 1 and left[0] in independent or len(right) == 1 and right[0] in independent
            if gutter.width < (0.25 if pane_side else 0.75) * scale:
                continue
            if (
                len(left) < 2
                and not (len(left) == 1 and left[0] in independent)
                or len(right) < 2
                and not (len(right) == 1 and right[0] in independent)
            ):
                continue
            if any(items[key].box.x < gutter.start and items[key].box.right > gutter.stop for key in spanning):
                return True
    return False


def _repeated_row_pair(first: tuple[str, ...], second: tuple[str, ...], alignment: AlignmentAnalysis) -> bool:
    """Recognize two rows whose shared columns explain their sole vertical gap.

    With no other row spacing to compare, a large gap alone cannot distinguish
    two records from two sections. Require two independent repeated columns;
    left, right and centre anchors for the same pair count as one column.
    """
    upper, lower = frozenset(first), frozenset(second)
    if len(_row_sets(upper, alignment)) != 1 or len(_row_sets(lower, alignment)) != 1:
        return False
    pairs = set()
    for anchor in alignment.anchors:
        a, b = upper.intersection(anchor.members), lower.intersection(anchor.members)
        if len(a) == len(b) == 1:
            pairs.add((next(iter(a)), next(iter(b))))
    return any(a != c and b != d for a, b in pairs for c, d in pairs)


def _cut_order(
    first: tuple[str, ...], second: tuple[str, ...], before: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Keep each region whole when native precedence permits a region sequence."""
    left, right = frozenset(first), frozenset(second)
    forward = any(a in left and b in right for a, b in before)
    backward = any(a in right and b in left for a, b in before)
    if forward and backward:
        return None
    return (second, first) if backward else (first, second)


def region_candidates(
    items: Sequence[LayoutItem],
    *,
    keep_together: Sequence[Sequence[str]] = (),
    before: Sequence[tuple[str, str]] = (),
    alignment: AlignmentAnalysis | None = None,
    independent: Sequence[str] = (),
) -> tuple[RegionCandidate, ...]:
    """Generate complete local region hypotheses without committing a split.

    Blank bands suggest horizontal or vertical partitions. Repeated cross-column
    rows protect possible forms and tables; complete explicit groups remain intact.
    Every hypothesis retains each source exactly once. A no-split alternative is
    always present. Explicit whole-pane identities can support a column with one
    measured outer rectangle while their internal controls remain encapsulated.

    Args:
        items: Distinct, fully measured units in one comparable coordinate space.
        keep_together: Confirmed unit groups that a region boundary must preserve.
            Independent vertical groups can establish separate reading columns.
        before: Confirmed precedence edges between keys in this local input.
        alignment: Existing analysis of exactly these units, when already available.
        independent: Whole native pane units, whose descendants are not layout peers.

    Returns:
        Uncommitted local layouts in deterministic preference order.

    Raises:
        ValueError: Keys, coordinate spaces, alignment membership or constraints
            contradict the supplied local input.
    """
    by_key = {item.key: item for item in items}
    if len(by_key) != len(items):
        raise ValueError("Geometric partition contains duplicate unit keys")
    if len({item.box.space for item in items}) > 1:
        raise ValueError("Geometric partition requires comparable coordinate spaces")
    selected = frozenset(by_key)
    groups = tuple(frozenset(group) for group in keep_together if group)
    edges = tuple(before)
    if any(not group <= selected for group in groups) or any(a not in selected or b not in selected for a, b in edges):
        raise ValueError("Geometric partition constraint is outside its input")
    alignment = alignment if alignment is not None else analyze_alignment(items)
    ordered = tuple(key for row in alignment.rows for key in row.members)
    if len(ordered) != len(selected) or frozenset(ordered) != selected:
        raise ValueError("Geometric partition alignment differs from its input")
    ordered = _stable_order(ordered, edges)
    if not items:
        return (RegionCandidate(RegionPlan(()), 0.0),)

    def candidates(keys: tuple[str, ...]) -> tuple[RegionCandidate, ...]:
        fallback = RegionCandidate(RegionPlan(_stable_order(keys, edges)), 0.0)
        if len(keys) < 2:
            return (fallback,)
        local = tuple(by_key[key] for key in keys)
        scale = median(item.box.height for item in local)
        vertical = _gaps(local, "vertical")
        horizontal = _gaps(local, "horizontal")
        ordinary_gap = median(gap.width for gap in horizontal) if horizontal else 0.0
        width = max(item.box.right for item in local) - min(item.box.x for item in local)
        key_set = frozenset(keys)
        candidates = []
        for gap in (*vertical, *horizontal):
            if gap.axis == "vertical":
                first = tuple(key for key in keys if by_key[key].box.right <= gap.start)
            else:
                first = tuple(key for key in keys if by_key[key].box.bottom <= gap.start)
            first_set = frozenset(first)
            second = tuple(key for key in keys if key not in first_set)
            if (
                not first
                or not second
                or any(group & first_set and group - first_set and group <= key_set for group in groups)
            ):
                continue
            if gap.axis == "vertical":
                pane_side = len(first) == 1 and first[0] in independent or len(second) == 1 and second[0] in independent
                minimum_gap = 0.25 * scale if pane_side else min(1.25 * scale, 0.06 * width)
                if gap.width < minimum_gap:
                    continue
            oriented = _cut_order(first, second, edges)
            if oriented is None:
                continue
            spanning = False
            if gap.axis == "vertical":
                if not _columns_supported(first, second, by_key, groups, alignment, frozenset(independent)):
                    continue
            else:
                spanning = _spanning_boundary(first, second, by_key, scale, frozenset(independent))
                if len(horizontal) == 1 and not spanning and _repeated_row_pair(first, second, alignment):
                    continue
                threshold = 1.5 * scale
                if len(horizontal) > 1:
                    threshold = max(threshold, 1.65 * ordinary_gap)
                if gap.width < threshold and not spanning:
                    continue
            balance = min(len(first), len(second)) / len(keys)
            score = gap.width / scale + 0.1 * balance + (1.0 if spanning else 0.0)
            candidates.append(_Cut(gap, *oriented, score))
        result = []
        for cut in sorted(candidates, key=lambda cut: (-cut.score, cut.gap.axis, cut.gap.start, cut.first, cut.second)):
            children = (RegionPlan(cut.first), RegionPlan(cut.second))
            proof = ("persistent-gutter",)
            if set(cut.first) <= set(independent) or set(cut.second) <= set(independent):
                proof = (*proof, "native-pane")
            result.append(
                RegionCandidate(RegionPlan((*cut.first, *cut.second), cut.gap.axis, children), cut.score, proof)
            )
        return (*result, fallback)

    return candidates(ordered)


def partition_regions(
    items: Sequence[LayoutItem],
    *,
    keep_together: Sequence[Sequence[str]] = (),
    before: Sequence[tuple[str, str]] = (),
    alignment: AlignmentAnalysis | None = None,
    independent: Sequence[str] = (),
) -> RegionPlan:
    """Select a bounded recursive layout from pure local partition candidates.

    Args:
        items: Distinct whole visual units in one comparable coordinate space.
        keep_together: Explicit associations that every candidate must preserve.
        before: Confirmed precedence that constrains the chosen reading order.
        alignment: Reusable row and anchor evidence for exactly these units.
        independent: Whole native pane identities supporting independent columns.

    Returns:
        A deterministic region tree with original membership retained once per leaf.
        Every level chooses among candidates before materializing any display block.
    """
    by_key = {item.key: item for item in items}
    groups, edges, panes = tuple(keep_together), tuple(before), tuple(independent)

    def select(keys: tuple[str, ...], depth: int) -> RegionPlan:
        local = tuple(by_key[key] for key in keys)
        selected = frozenset(keys)
        options = region_candidates(
            local,
            keep_together=tuple(group for group in groups if set(group) <= selected),
            before=tuple((a, b) for a, b in edges if a in selected and b in selected),
            alignment=alignment if len(keys) == len(items) else None,
            independent=tuple(key for key in panes if key in selected),
        )
        chosen = options[0].plan
        if not chosen.children or depth >= 32:
            return RegionPlan(chosen.keys)
        children = tuple(select(child.keys, depth + 1) for child in chosen.children)
        return RegionPlan(tuple(key for child in children for key in child.keys), chosen.axis, children)

    # Validate the complete contract before dropping constraints outside a recursive
    # child. Duplicate keys and out-of-scope relationships remain explicit errors.
    region_candidates(items, keep_together=groups, before=edges, alignment=alignment, independent=panes)
    return select(tuple(by_key), 0)
