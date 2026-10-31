# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Adapt frozen UI facts to the geometry-only layout algorithms."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, analyze_alignment
from computer_use.interaction.read.presentation.interface.geometric.model import GeometricScopePlan
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.geometric.scope_units import measured_units, source_only
from computer_use.interaction.read.presentation.interface.geometric.document_flow import document_flow_scope
from computer_use.interaction.read.presentation.interface.helpers import same_scope
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources

_LAYOUT_ROLES = frozenset(
    ("window", "sheet", "group", "splitGroup", "tabPanel", "scrollArea", "navigation", "webArea", "application")
)


def scope_plan(
    draft: InterfaceDraft, key: str, blocks: Mapping[str, InterfaceBlock] | None = None
) -> GeometricScopePlan | None:
    """Prepare comparable local segments while retaining unpositioned children.

    Args:
        draft: Captured identities, semantic boundaries and local display blocks.
        key: Existing parent whose direct children may be visually arranged.
        blocks: Current immutable view while a stage constructs new local groups.

    Returns:
        Geometry-only candidates, explicit constraints and native-order barriers,
        or None when semantic scope cannot establish safe relative placement.
        Missing rectangles divide an otherwise valid scope into local segments;
        they do not remove the unpositioned sources or cancel sibling analysis.
    """
    blocks = draft.blocks if blocks is None else blocks
    parent = blocks[key]
    if (
        not draft.enabled
        or len(parent.children) < 2
        or parent.layout not in ("tree", "fields", "field-inline")
        or parent.kind in ("table", "collection", "record")
        or parent.visual_unit
    ):
        return None
    if document_flow_scope(key, draft, blocks):
        return None
    if parent.primary_ref is not None:
        ref = parent.primary_ref
        if draft.projected_nodes[ref]["role"] not in _LAYOUT_ROLES or draft.evidence[ref].hidden:
            return None
    elif parent.kind not in ("field", "region"):
        return None
    keys = parent.children
    owners = {ref: child for child in keys for ref in block_sources(child, blocks)}
    if not owners or not same_scope(draft, tuple(owners)):
        return None
    measured = measured_units(keys, draft, blocks)
    measured_keys = tuple(child for child in keys if child in measured)
    if not measured_keys:
        return None
    if len({box.space for box in measured.values()}) != 1:
        return None
    bounds = draft.source.geometry.boxes.get(parent.primary_ref)
    if bounds is not None:
        # Off-viewport sources keep their original place as local barriers. Their
        # rectangles do not disable the known in-viewport siblings around them.
        measured = {child: box for child, box in measured.items() if bounds.contains(box)}
        measured_keys = tuple(child for child in measured_keys if child in measured)
        if not measured_keys:
            return None
    items = tuple(LayoutItem(child, measured[child]) for child in measured_keys)
    alignment = analyze_alignment(items)
    bands = {member: index for index, band in enumerate(alignment.rows) for member in band.members}
    if any(
        measured[a].intersection_area(measured[b]) > 0
        and bands[a] != bands[b]
        and not measured[a].contains(measured[b])
        and not measured[b].contains(measured[a])
        for index, a in enumerate(measured_keys)
        for b in measured_keys[index + 1 :]
    ):
        return None
    before = native_edges(keys, owners, draft.relations)
    if before is None:
        return None
    protected: set[tuple[str, ...]] = set()
    for relation in draft.relations:
        if relation.kind in ("flows_to", "flows_from"):
            endpoints = {relation.source, *relation.targets}
            if endpoints.intersection(owners) and not endpoints <= owners.keys():
                return None
        if (
            relation.kind in ("labelled_by", "labels", "described_by", "describes")
            and relation.complete
            and relation.confirmed
        ):
            members = tuple(sorted({owners[ref] for ref in (relation.source, *relation.targets) if ref in owners}))
            if len(members) > 1:
                protected.add(members)
    for ref, owner in owners.items():
        node = draft.projected_nodes[ref]
        targets = (node.get("titleRef"), node.get("visualLabelRef"), *node.get("labelTargets", ()))
        for target in targets:
            if isinstance(target, str) and target in owners and owners[target] != owner:
                protected.add(tuple(sorted((owner, owners[target]))))
    if parent.primary_ref is not None:
        for group in draft.projected_nodes[parent.primary_ref].get("layoutGroups", ()):
            members = tuple(sorted({owners[ref] for ref in group.get("members", ()) if ref in owners}))
            if len(members) > 1:
                protected.add(members)
    unplaced = tuple(child for child in keys if child not in measured and not source_only(blocks[child]))
    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    for child in keys:
        if source_only(blocks[child]):
            continue
        if child in measured:
            if current and not same_region(
                draft, (ref for member in (*current, child) for ref in block_sources(member, blocks))
            ):
                segments.append(tuple(current))
                current.clear()
            current.append(child)
        elif current:
            segments.append(tuple(current))
            current.clear()
    if current:
        segments.append(tuple(current))
    # Captured position is the only available context for an unmeasured source.
    # Native flow that requires crossing such a boundary cannot be reconciled
    # by a geometry-only move, so preserve the original layout for this scope.
    segment_of = {child: index for index, segment in enumerate(segments) for child in segment}
    positions = {child: index for index, child in enumerate(keys)}
    if any(segment_of.get(a) != segment_of.get(b) and positions[a] > positions[b] for a, b in before):
        return None
    return GeometricScopePlan(
        items, alignment, before, tuple(sorted(protected)), tuple(keys), unplaced, tuple(segments)
    )


def collect_scope_plans(draft: InterfaceDraft) -> InterfaceDraft:
    """Publish one row/anchor analysis for each currently eligible local scope."""
    plans = {key: plan for key in draft.blocks if (plan := scope_plan(draft, key)) is not None}
    return replace(draft, geometric_alignment=MappingProxyType(plans))
