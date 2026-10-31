# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply geometry-only region plans to source-preserving interface blocks."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.candidates import scope_plan
from computer_use.interaction.read.presentation.interface.geometric.model import (
    RegionLayoutEvidence,
    RegionScopeEvidence,
)
from computer_use.interaction.read.presentation.interface.geometric.partition import (
    RegionPlan,
    leaf_paths,
    partition_regions,
)
from computer_use.interaction.read.presentation.interface.geometric.panes import comparable_pane_columns, pane_members
from computer_use.interaction.read.presentation.interface.geometric.model import GeometricScopePlan
from computer_use.interaction.read.presentation.interface.helpers import revise, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import stable_order
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources


def _relation_plan(draft: InterfaceDraft, key: str, plan: GeometricScopePlan) -> GeometricScopePlan:
    """Consume the published local graph only while its membership is current."""
    graph = draft.geometric_relations
    relation = graph.scopes.get(key) if graph is not None else None
    if relation is None or relation.source_order != plan.source_order:
        return plan
    return replace(plan, before=relation.before, keep_together=relation.keep_together, segments=relation.segments)


def _describe_native_panes(
    draft: InterfaceDraft, blocks: dict[str, InterfaceBlock]
) -> tuple[dict[str, InterfaceBlock], tuple[str, ...]]:
    """Annotate known sibling panes without reparenting their source contents."""
    decisions = []
    for parent in tuple(blocks.values()):
        if parent.kind in ("table", "collection", "record"):
            continue
        panes = comparable_pane_columns(parent.children, draft, blocks)
        if not panes:
            continue
        for index, key in enumerate(panes):
            block = blocks[key]
            position = "left" if index == 0 else "right" if index == len(panes) - 1 else "middle"
            if block.position != position:
                blocks[key] = replace(block, position=position)
                decisions.append("geometric-native-pane:" + key + ":" + position)
    return blocks, tuple(decisions)


def canonicalize_scopes(draft: InterfaceDraft) -> InterfaceDraft:
    """Place comparable units in visual order before testing field membership.

    Native precedence remains authoritative. The captured enumeration is a stable
    fallback when geometry cannot establish a local scope; it is not an implicit
    precedence edge inside a complete unordered form.
    """
    blocks, decisions = dict(draft.blocks), []
    for key, parent in tuple(blocks.items()):
        plan = draft.geometric_alignment.get(key)
        if plan is None or parent.children != plan.source_order:
            plan = scope_plan(draft, key, blocks)
        if plan is None:
            continue
        plan = _relation_plan(draft, key, plan)
        ordered = list(parent.children)
        positions = {child: index for index, child in enumerate(parent.children)}
        for segment in plan.segments:
            members = frozenset(segment)
            candidates = tuple(member for band in plan.alignment.rows for member in band.members if member in members)
            local = stable_order(candidates, tuple((a, b) for a, b in plan.before if a in members and b in members))
            if local is not None:
                for original, replacement in zip(segment, local):
                    ordered[positions[original]] = replacement
        ordered = tuple(ordered)
        if ordered != parent.children:
            blocks[key] = replace(parent, children=ordered)
            decisions.append("geometric-source-order:" + key)
    return revise(draft, blocks, *decisions)


def arrange_regions(draft: InterfaceDraft) -> InterfaceDraft:
    """Materialize recursive columns while keeping horizontal bands compact.

    Every accepted horizontal and vertical boundary remains in immutable evidence.
    Horizontal bands determine order without adding headings; independent columns
    receive neutral visible regions. Existing fields and controls remain whole.
    """
    blocks, decisions = dict(draft.blocks), []
    scopes = dict(draft.geometric_regions.scopes) if draft.geometric_regions is not None else {}
    for key, parent in tuple(draft.blocks.items()):
        if parent.kind == "field" or key in scopes:
            continue
        prepared = scope_plan(draft, key, blocks)
        if prepared is None:
            continue
        prepared = _relation_plan(draft, key, prepared)
        measured = {item.key: item for item in prepared.items}
        unit_sources = {child: block_sources(child, blocks) for child in parent.children}

        def materialize(region: RegionPlan, owner: str) -> tuple[str, ...]:
            if not region.children:
                return region.keys
            if region.axis == "horizontal":
                return tuple(member for child in region.children for member in materialize(child, owner))
            children = []
            physical = sorted(region.children, key=lambda child: min(measured[member].box.x for member in child.keys))
            for child in region.children:
                child_key = synthetic_key("geometric-region", owner, child.keys)
                members = materialize(child, child_key)
                position = "left" if child is physical[0] else "right" if child is physical[-1] else "middle"
                blocks[child_key] = InterfaceBlock(
                    child_key, "region", None, (), members, origin="visual", position=position, region_role="region"
                )
                children.append(child_key)
            return tuple(children)

        replacements: dict[str, tuple[str, ...]] = {}
        consumed: set[str] = set()
        segment_trees: dict[str, RegionPlan] = {}
        segment_members: set[str] = set()
        for segment in prepared.segments:
            members = frozenset(segment)
            segment_members.update(segment)
            # A confirmed association crossing an unknown-position barrier stays
            # under its native parent instead of receiving a partial visual region.
            if any(members.intersection(group) and not set(group) <= members for group in prepared.keep_together):
                segment_trees[segment[0]] = RegionPlan(segment)
                continue
            items = tuple(measured[member] for member in segment)
            plan = partition_regions(
                items,
                keep_together=tuple(group for group in prepared.keep_together if set(group) <= members),
                before=tuple((a, b) for a, b in prepared.before if a in members and b in members),
                alignment=prepared.alignment if len(items) == len(prepared.items) else None,
                independent=pane_members(segment, draft, blocks),
            )
            segment_trees[segment[0]] = plan
            replacements[segment[0]] = materialize(plan, key)
            consumed.update(segment)
        forest = tuple(
            segment_trees[child] if child in segment_trees else RegionPlan((child,))
            for child in parent.children
            if child in segment_trees or child not in segment_members
        )
        paths = leaf_paths(forest)
        if set(paths) != set(parent.children):
            raise ValueError("Geometric region forest lost an original reading unit")
        source_paths = {}
        for child, path in paths.items():
            for ref in unit_sources[child]:
                if ref in source_paths:
                    raise ValueError("Geometric region sources occur in multiple reading units")
                source_paths[ref] = path
        scopes[key] = RegionScopeEvidence(forest, source_paths)
        children = tuple(
            member
            for child in parent.children
            for member in replacements.get(child, () if child in consumed else (child,))
        )
        if children != parent.children:
            blocks[key] = replace(parent, children=children)
            decisions.append("geometric-regions:" + key)
    blocks, pane_decisions = _describe_native_panes(draft, blocks)
    result = revise(draft, blocks, *decisions, *pane_decisions)
    return replace(result, geometric_regions=RegionLayoutEvidence(scopes)) if scopes else result
