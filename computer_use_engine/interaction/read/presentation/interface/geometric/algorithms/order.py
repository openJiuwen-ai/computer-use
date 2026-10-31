# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Order already-proved local visual rows without changing native flow precedence."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.algorithms.rows import row_geometry
from computer_use.interaction.read.presentation.interface.helpers import revise
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import (
    add_compatible_edge,
    native_edges,
    stable_order,
)
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources, comparable_members


def _external_flow(refs: frozenset[str], draft: InterfaceDraft) -> bool:
    """Keep captured order when a declared flow extends outside this local row."""
    for relation in draft.relations:
        if relation.kind not in ("flows_to", "flows_from"):
            continue
        endpoints = frozenset((relation.source, *relation.targets))
        if endpoints.intersection(refs) and not endpoints <= refs:
            return True
    return False


def order_visual_rows(draft: InterfaceDraft) -> InterfaceDraft:
    """Apply horizontal order inside complete visual rows, after native constraints.

    Args:
        draft: Source-bound row membership from GeometricRowPass. Native logical
            collections, scope changes, incomplete geometry and unknown flow retain
            their existing order.

    Returns:
        A draft with row-local child order only. Native flow edges are authoritative;
        a geometric edge is added only when it cannot contradict those constraints.
        No source facts, element identifiers or parent-block positions change.
    """
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    for key, block in draft.blocks.items():
        original = block.children
        if (
            block.layout != "visual-row"
            or block.primary_ref is not None
            or block.source_refs
            or len(original) < 2
            or not row_geometry(original, draft, blocks, require_source_order=False)
        ):
            continue
        measured = comparable_members(original, draft, blocks)
        if measured is None:
            continue
        owners = {ref: member for member in original for ref in block_sources(member, blocks)}
        if _external_flow(frozenset(owners), draft):
            continue
        native = native_edges(original, owners, draft.relations)
        if native is None:
            continue
        edges: dict[str, set[str]] = {member: set() for member in original}
        for before, after in native:
            edges[before].add(after)
        horizontal = sorted(original, key=lambda member: measured[member].x)
        for before, after in zip(horizontal, horizontal[1:]):
            add_compatible_edge(edges, before, after)
        ordered = stable_order(
            original, tuple((before, after) for before, targets in edges.items() for after in targets)
        )
        assert ordered is not None
        if ordered != original:
            blocks[key] = replace(block, children=ordered)
            decisions.append("geometric-row-order:" + key)
    return revise(draft, blocks, *decisions)
