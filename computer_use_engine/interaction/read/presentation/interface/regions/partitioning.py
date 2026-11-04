# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Find independent local columns while preserving stronger semantic constraints."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges, stable_order
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources, comparable_members
from computer_use.interaction.read.presentation.spatial.geometry import Box


@dataclass(frozen=True)
class LocalColumn:
    """A complete set of reading units separated by a persistent vertical gap.

    Attributes:
        members: Existing complete child blocks, in their captured order.
        position: Relative place among the proven parallel columns.
    """

    members: tuple[str, ...]
    position: str


def _crosses_relation(
    groups: Sequence[tuple[str, ...]], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]
) -> bool:
    """Keep established label membership inside one proposed reading region."""
    owners = {ref: index for index, group in enumerate(groups) for key in group for ref in block_sources(key, blocks)}
    for ref, owner in owners.items():
        node = draft.projected_nodes[ref]
        related = [node.get("titleRef"), node.get("visualLabelRef"), *node.get("labelTargets", ())]
        if any(target in owners and owners[target] != owner for target in related if isinstance(target, str)):
            return True
    for relation in draft.relations:
        if relation.kind not in ("labelled_by", "labels", "described_by", "describes"):
            continue
        owner = owners.get(relation.source)
        if owner is not None and any(target in owners and owners[target] != owner for target in relation.targets):
            return True
    return False


def _aligned_rows(groups: Sequence[tuple[str, ...]], bounds: Mapping[str, Box]) -> bool:
    """Recognize repeated cross-column row bands that compete with column reading."""
    for left, right in zip(groups, groups[1:]):
        aligned = [
            (first, second)
            for first in left
            for second in right
            if bounds[first].vertical_overlap(bounds[second]) >= 0.6
        ]
        if len({first for first, _ in aligned}) >= 2 and len({second for _, second in aligned}) >= 2:
            return True
    return False


def _vertical_run(members: Sequence[str], bounds: Mapping[str, Box]) -> bool:
    """Require separate vertical bands, rather than two pieces of one visual row."""
    ordered = sorted(members, key=lambda key: bounds[key].y)
    for index, before in enumerate(ordered):
        first = bounds[before]
        for after in ordered[index + 1 :]:
            second = bounds[after]
            if second.y - first.bottom >= 0.25 * min(first.height, second.height):
                return True
    return False


def local_columns(
    keys: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]
) -> tuple[LocalColumn, ...]:
    """Recognize two or three persistent columns in a complete unordered scope.

    Large gaps must span the complete local collection, exceed the local item
    height, and contain multiple members on each side. Interleaved source order
    may be regrouped when the columns have independent vertical runs. Repeated
    aligned rows, conflicting native flow, and cross-column field membership
    preserve the original organization. Each original unit stays intact.
    """
    if len(keys) < 4:
        return ()
    measured = comparable_members(keys, draft, blocks)
    if measured is None:
        return ()
    local_owners = {ref: key for key in keys for ref in block_sources(key, blocks)}
    if native_edges(keys, local_owners, draft.relations) is None:
        return ()
    if any(
        measured[first].intersection_area(measured[second]) > 0
        for index, first in enumerate(keys)
        for second in keys[index + 1 :]
    ):
        return ()
    ordered = sorted(keys, key=lambda key: measured[key].x)
    span = max(box.right for box in measured.values()) - min(box.x for box in measured.values())
    minimum_gap = max(1.5 * median(box.height for box in measured.values()), 0.08 * span)
    groups: list[list[str]] = [[]]
    right = measured[ordered[0]].right
    for key in ordered:
        box = measured[key]
        if groups[-1] and box.x - right >= minimum_gap:
            groups.append([])
        groups[-1].append(key)
        right = max(right, box.right)
    if len(groups) not in (2, 3) or any(len(group) < 2 for group in groups):
        return ()
    if any(not _vertical_run(group, measured) for group in groups):
        return ()
    source_positions = {key: index for index, key in enumerate(keys)}
    sorted_groups = [tuple(sorted(group, key=source_positions.__getitem__)) for group in groups]
    interleaved = any(
        max(source_positions[key] for key in group) - min(source_positions[key] for key in group) + 1 != len(group)
        for group in sorted_groups
    )
    if interleaved and _aligned_rows(sorted_groups, measured):
        return ()
    # Two independent vertical runs must actually coexist vertically. A diagonal
    # sequence is still a sequence, even when its X projections happen to separate.
    verticals = [
        (min(measured[key].y for key in group), max(measured[key].bottom for key in group)) for group in sorted_groups
    ]
    for left, right_band in zip(verticals, verticals[1:]):
        overlap = min(left[1], right_band[1]) - max(left[0], right_band[0])
        if overlap < 0.6 * min(left[1] - left[0], right_band[1] - right_band[0]):
            return ()
    if _crosses_relation(sorted_groups, draft, blocks):
        return ()
    positions = ("left", "right") if len(sorted_groups) == 2 else ("left", "middle", "right")
    columns = [LocalColumn(group, position) for group, position in zip(sorted_groups, positions)]
    # Native flow must still be expressible after treating each column as a unit.
    # Alternating L→R→L edges would create a cycle and veto the partition.
    by_key = {column.members[0]: column for column in columns}
    column_keys = tuple(sorted(by_key, key=source_positions.__getitem__))
    owners = {
        ref: column.members[0] for column in columns for key in column.members for ref in block_sources(key, blocks)
    }
    edges = native_edges(column_keys, owners, draft.relations)
    if edges is None:
        return ()
    order = stable_order(column_keys, edges)
    assert order is not None
    return tuple(by_key[key] for key in order)
