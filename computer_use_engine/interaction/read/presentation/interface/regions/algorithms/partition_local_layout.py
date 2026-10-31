# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build neutral regions from substantial gaps in a local semantic scope."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.partitioning import local_columns
from computer_use.interaction.read.presentation.interface.regions.scope import ordered_ancestry


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Partition unordered layout scopes while preserving every original block."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    for key, block in tuple(blocks.items()):
        if block.primary_ref is None or len(block.children) < 4:
            continue
        node = draft.projected_nodes[block.primary_ref]
        if node["role"] not in ("window", "sheet", "group", "splitGroup", "tabPanel"):
            continue
        if node.get("layoutGroups") or draft.evidence[block.primary_ref].incomplete:
            continue
        if ordered_ancestry(block.primary_ref, draft):
            continue
        columns = local_columns(block.children, draft, blocks)
        if not columns:
            continue
        children = []
        for column in columns:
            region_key = synthetic_key("column-region", key, column.members)
            blocks[region_key] = InterfaceBlock(
                region_key,
                "region",
                None,
                (),
                column.members,
                origin="visual",
                position=column.position,
                region_role="region",
            )
            children.append(region_key)
        blocks[key] = replace(block, children=tuple(children))
        decisions.append("column-regions:" + block.primary_ref)
    return revise(draft, blocks, *decisions)
