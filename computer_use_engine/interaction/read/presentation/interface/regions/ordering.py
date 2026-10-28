# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable local reading order with native precedence and weaker geometric edges."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import (
    add_compatible_edge,
    native_edges,
    stable_order,
)
from computer_use.interaction.read.presentation.interface.regions.scope import (
    block_sources,
    comparable_members,
    linked_members,
)


def ordered_children(
    keys: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock], *, geometry: bool = True
) -> tuple[str, ...]:
    """Order comparable local units without reversing native semantic precedence.

    Complete native flow wins. Geometry supplies only well-separated vertical
    precedence inside overlapping horizontal bands; horizontal ties keep source
    order. Each weak edge is rejected if it would conflict with stronger evidence.
    Partial or contradictory native order keeps the captured sequence unchanged.

    Args:
        keys: Whole reading units within one original semantic scope.
        draft: Immutable source facts and the current interface evidence.
        blocks: Current display organization before this ordering decision.
        geometry: Permit local geometric edges after native constraints are checked.
    """
    original = tuple(keys)
    if len(original) < 2:
        return original
    owners = {ref: key for key in original for ref in block_sources(key, blocks)}
    native = native_edges(original, owners, draft.relations)
    if native is None:
        return original
    edges: dict[str, set[str]] = {key: set() for key in original}
    for before, after in native:
        edges[before].add(after)
    measured = comparable_members(original, draft, blocks) if geometry and not linked_members(owners, draft) else None
    # Overlap can express overlays or composite controls. It supplies no order.
    if measured is not None and not any(
        measured[first].intersection_area(measured[second]) > 0
        for index, first in enumerate(original)
        for second in original[index + 1 :]
    ):
        positions = {key: index for index, key in enumerate(original)}
        vertical = sorted(original, key=lambda key: (measured[key].y, positions[key]))
        for index, before in enumerate(vertical):
            first = measured[before]
            for after in vertical[index + 1 :]:
                second = measured[after]
                if first.horizontal_overlap(second) >= 0.6 and second.y - first.bottom >= 0.25 * min(
                    first.height, second.height
                ):
                    add_compatible_edge(edges, before, after)
    result = stable_order(original, tuple((key, target) for key, targets in edges.items() for target in targets))
    assert result is not None
    return result
