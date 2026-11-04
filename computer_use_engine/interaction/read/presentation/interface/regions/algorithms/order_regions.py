# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply confirmed local reading constraints within existing semantic regions."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.ordering import ordered_children
from computer_use.interaction.read.presentation.interface.regions.scope import (
    block_bounds,
    block_sources,
    ordered_ancestry,
    reading_scope,
)
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Order ordinary region children while retaining native collections and records."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    for key, block in tuple(blocks.items()):
        if block.kind in ("table", "collection", "record") or block.layout != "tree":
            continue
        refs = tuple(ref for child in block.children for ref in block_sources(child, blocks))
        if (
            not refs
            or any(ordered_ancestry(ref, draft) for ref in refs)
            or len({reading_scope(ref, draft) for ref in refs}) != 1
        ):
            continue
        # Region planning has already ordered distinct leaves under native flow.
        # A later local sort cannot flatten them back into one geometric surface.
        if not same_region(draft, refs):
            continue
        bounds = draft.source.geometry.boxes.get(block.primary_ref)
        members = [block_bounds(child, draft, blocks) for child in block.children]
        inside = bounds is None or all(member is not None and bounds.contains(member) for member in members)
        # Providers can report scrolled-off content next to fixed footer controls.
        # Their absolute Y positions do not establish one shared reading surface.
        ordered = ordered_children(block.children, draft, blocks, geometry=block.label_ref is None and inside)
        if ordered != block.children:
            blocks[key] = replace(block, children=ordered)
            decisions.append("interface-reading-order:" + key)
    return revise(draft, blocks, *decisions)
