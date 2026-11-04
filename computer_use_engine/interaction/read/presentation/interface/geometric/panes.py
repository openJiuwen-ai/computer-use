# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read existing native panes as whole visual regions without changing ownership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import block_bounds
from computer_use.interaction.read.presentation.spatial.geometry import Box


def pane_members(keys: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> tuple[str, ...]:
    """Find explicit independent content surfaces among sibling reading units.

    An input field beside its label does not establish two panes. Existing scroll,
    web and split surfaces, or already-evidenced left/middle/right regions, retain
    their complete child trees while supplying their outer box to region analysis.
    """
    result = []
    for key in keys:
        block = blocks[key]
        if block.primary_ref is None or block.layout == "source-only":
            continue
        node = draft.projected_nodes[block.primary_ref]
        if draft.evidence[block.primary_ref].hidden or node.get("modal") is True:
            continue
        role = node["role"]
        if role in ("scrollArea", "splitGroup", "webArea") or (
            block.kind == "region" and block.position in ("left", "middle", "right")
        ):
            result.append(key)
    return tuple(result)


def comparable_pane_columns(
    keys: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]
) -> tuple[str, ...]:
    """Return side-by-side native pane identities in physical left-to-right order.

    Pane height overlap and disjoint horizontal extents support relative positions.
    Small border overlaps tolerate native frame padding. Interleaved text and
    separators keep their existing source positions and do not become pane members.
    """
    candidates = pane_members(keys, draft, blocks)
    measured: dict[str, Box] = {}
    for key in candidates:
        box = block_bounds(key, draft, blocks)
        if box is not None:
            measured[key] = box
    candidates = tuple(measured)
    if len(candidates) < 2 or len({measured[key].space for key in candidates}) != 1:
        return ()
    signatures = set()
    for key in candidates:
        ref = blocks[key].primary_ref
        assert ref is not None
        signatures.add((draft.evidence[ref].scope, tuple(draft.projected_nodes[ref].get("blockedBy", ()))))
    if len(signatures) != 1:
        return ()
    ordered = tuple(sorted(candidates, key=lambda key: (measured[key].x, key)))
    for index, first in enumerate(ordered):
        a = measured[first]
        for second in ordered[index + 1 :]:
            b = measured[second]
            overlap = min(a.bottom, b.bottom) - max(a.y, b.y)
            if overlap < 0.6 * min(a.height, b.height):
                return ()
            if a.right > b.x + 0.05 * min(a.width, b.width):
                return ()
    return ordered
