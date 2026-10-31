# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate selected layout explanations against their retained source membership."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.patterns.model import PatternPlan
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.model import LayoutIssue


def _reason(plan: PatternPlan, draft: InterfaceDraft) -> str | None:
    blocks = draft.blocks
    owner = blocks.get(plan.owner_key)
    if owner is None or owner.primary_ref != plan.owner_ref or plan.owner_ref not in draft.projected_nodes:
        return "selected pattern owner differs from its real source"
    inputs = plan.input_keys
    if not inputs or len(inputs) != len(set(inputs)) or any(key not in blocks for key in inputs):
        return "selected pattern lost its original input inventory"
    expected = set()
    pending = list(draft.projected_nodes[plan.owner_ref].get("children", ()))
    while pending:
        ref = pending.pop()
        if ref in expected or ref not in draft.projected_nodes:
            return "selected pattern owner has conflicting original membership"
        expected.add(ref)
        pending.extend(draft.projected_nodes[ref].get("children", ()))
    supplied = tuple(ref for key in inputs for ref in block_sources(key, blocks))
    if len(supplied) != len(set(supplied)) or set(supplied) != expected:
        return "selected pattern input differs from the original owner scope"
    selected = (
        tuple(member for row in plan.rows for member in row.members)
        if plan.kind == "grid"
        else tuple(row.key for row in plan.rows)
    )
    remaining = plan.remaining_keys
    if (
        not selected
        or len(selected) != len(set(selected))
        or len(remaining) != len(set(remaining))
        or set(selected).intersection(remaining)
        or set((*selected, *remaining)) != set(inputs)
    ):
        return "selected pattern duplicated, invented or omitted an original member"
    if plan.kind == "grid":
        grids = tuple(blocks[key] for key in owner.children if blocks[key].layout == "visual-grid")
        row_keys = tuple(row.key for row in plan.rows)
        if (
            len(grids) != 1
            or grids[0].children != row_keys
            or len(grids[0].columns) != plan.column_count
            or plan.column_count < 2
        ):
            return "selected grid differs from its materialized rows or columns"
        for row in plan.rows:
            block = blocks.get(row.key)
            if (
                row.ref is not None
                or block is None
                or block.primary_ref is not None
                or block.source_refs
                or block.layout != "visual-row"
                or block.children != row.members
                or block.column_slots != row.slots
                or len(row.members) != len(row.slots)
                or tuple(sorted(set(row.slots))) != row.slots
                or any(type(slot) is not int or not 0 <= slot < plan.column_count for slot in row.slots)
            ):
                return "selected grid row changed its independently sourced units"
    elif plan.kind in ("table", "cards", "list"):
        for row in plan.rows:
            block = blocks.get(row.key)
            if (
                block is None
                or row.ref is None
                or block.primary_ref != row.ref
                or draft.projected_nodes[row.ref]["role"] != "row"
                or any(key not in blocks for key in row.members)
            ):
                return "selected native item changed its original row identity"
            if plan.kind == "table":
                children = tuple(draft.projected_nodes[row.ref].get("children", ()))
                if tuple(blocks[key].primary_ref for key in row.members) != children or len(row.slots) != len(
                    row.members
                ):
                    return "selected table row changed its original cells"
                for key, slot in zip(row.members, row.slots):
                    ref = blocks[key].primary_ref
                    column = draft.projected_nodes[ref].get("columnRange", {})
                    if type(slot) is not int or column.get("location") != slot or column.get("length") != 1:
                        return "selected table column differs from its native extent"
            elif block.children != row.members:
                return "selected card changed its original content members"
    else:
        return "selected pattern has an unknown layout kind"
    for path in plan.wrapper_chains:
        if (
            len(path) < 2
            or len(set(path)) != len(path)
            or path[0] not in selected
            or any(key not in blocks or blocks[key].primary_ref not in expected for key in path)
        ):
            return "selected pattern has an invalid source wrapper path"
        for parent_key, child_key in zip(path, path[1:]):
            parent, child = blocks[parent_key].primary_ref, blocks[child_key].primary_ref
            if (
                parent is None
                or child is None
                or tuple(draft.projected_nodes[parent].get("children", ())) != (child,)
                or draft.projected_parents.get(child) != parent
            ):
                return "selected pattern wrapper path crosses an original parent"
    return None


def pattern_issues(draft: InterfaceDraft) -> tuple[LayoutIssue, ...]:
    """Return local plan-contract failures without weakening source validation.

    Args:
        draft: The final block graph and the typed plans that produced it.

    Returns:
        Issues at the original collection owner. The shared recovery gate can
        discard that local explanation while retaining all captured source atoms.
    """
    issues = []
    owners = set()
    for plan in draft.geometric_patterns:
        reason = _reason(plan, draft)
        if plan.owner_key in owners:
            reason = "several selected patterns claim the same source owner"
        owners.add(plan.owner_key)
        if reason is not None:
            key = (
                plan.owner_key
                if plan.owner_key in draft.blocks
                else next(
                    (key for key, block in draft.blocks.items() if plan.owner_ref in block.source_refs), draft.roots[0]
                )
            )
            issues.append(LayoutIssue(key, reason))
    return tuple(issues)
