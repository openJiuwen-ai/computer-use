# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check compound display units against unchanged native paths and control facts."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.compound.evidence import (
    CONTROL_ROLES,
    MEMBER_ROLES,
    close_wrapper,
    confirmed_edge,
    contained_caption,
    one_window,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft


def compound_issue(block: InterfaceBlock, draft: InterfaceDraft) -> str | None:
    """Verify the exact real targets behind a flattened visual control.

    Args:
        block: Selected compound with a principal control and flat auxiliary atoms.
        draft: Immutable original fields, relationships and geometry.

    Returns:
        A local recovery reason, or None when every source is represented exactly
        once under the same original single-child control path.
    """
    if (
        block.kind != "field"
        or block.primary_ref is not None
        or block.source_refs
        or not block.visual_unit
        or block.field_target_ref is None
    ):
        return "compound lacks its independently referenced principal control"
    members = tuple(draft.blocks[key] for key in block.children)
    refs = tuple(member.primary_ref for member in members)
    target = block.field_target_ref
    if (
        len(refs) < 2
        or any(ref is None for ref in refs)
        or len(set(refs)) != len(refs)
        or refs[0] != target
        or any(member.children or member.source_refs != (member.primary_ref,) for member in members)
        or set(refs) != {ref for ref, _ in block.member_roles}
    ):
        return "compound lost or duplicated an original auxiliary source"
    source_refs = tuple(ref for ref in refs if ref is not None)
    nodes = draft.projected_nodes
    controls = tuple(ref for ref in source_refs if nodes[ref]["role"] in CONTROL_ROLES)
    if (
        controls != (target,)
        or any(nodes[ref]["role"] not in MEMBER_ROLES for ref in source_refs)
        or any(
            nodes[ref].get("modal") is True or nodes[ref].get("subrole") in ("dialog", "systemDialog", "popover")
            for ref in source_refs
        )
        or not one_window(draft, source_refs)
    ):
        return "compound crossed a semantic or window boundary"
    roots = tuple(ref for ref in source_refs if draft.projected_parents.get(ref) not in refs)
    if len(roots) != 1 or nodes[roots[0]]["role"] != "group":
        return "compound lacks one original enclosing group"
    ordered = []
    current = roots[0]
    while current not in ordered:
        ordered.append(current)
        children = tuple(nodes[current].get("children", ()))
        original = draft.source.native_nodes[current]
        original_children = tuple(child["ref"] for child in original.get("children", ()))
        if children != original_children or len(children) > 1:
            return "compound changed an original native branch"
        if not children:
            break
        child = children[0]
        if child not in refs or not confirmed_edge(draft, current, child):
            return "compound crossed an unconfirmed original parent"
        current = child
    if len(ordered) != len(refs) or set(ordered) != set(refs):
        return "compound omitted part of its original native path"
    target_index = ordered.index(target)
    if (
        target_index == 0
        or any(nodes[ref]["role"] != "group" for ref in ordered[:target_index])
        or any(nodes[ref]["role"] not in ("group", "staticText", "image") for ref in ordered[target_index + 1 :])
    ):
        return "compound has an independent semantic member beside its principal control"
    boxes = {ref: draft.source.geometry.boxes.get(ref) for ref in source_refs}
    if (
        any(box is None for box in boxes.values())
        or any(not close_wrapper(boxes[ref], boxes[target]) for ref in ordered[:target_index])
        or any(not contained_caption(boxes[ref], boxes[target]) for ref in ordered[target_index + 1 :])
    ):
        return "compound source rectangles no longer describe one visual control"
    return None
