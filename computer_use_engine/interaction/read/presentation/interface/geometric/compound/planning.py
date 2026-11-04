# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select exact single-control paths without equating their independent actions."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.compound.evidence import (
    CONTROL_ROLES,
    MEMBER_ROLES,
    close_wrapper,
    complete_children,
    confirmed_edge,
    contained_caption,
    one_window,
)
from computer_use.interaction.read.presentation.interface.geometric.compound.model import CompoundControl
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft


def _candidate(draft: InterfaceDraft, key: str) -> CompoundControl | None:
    """Prove one whole original group-to-control path before changing display parents."""
    first = draft.blocks[key]
    if (
        first.primary_ref is None
        or first.source_refs != (first.primary_ref,)
        or first.layout != "tree"
        or draft.projected_nodes[first.primary_ref]["role"] != "group"
    ):
        return None
    members, refs = [], []
    current = key
    seen = set()
    while True:
        block = draft.blocks[current]
        ref = block.primary_ref
        if (
            current in seen
            or ref is None
            or block.source_refs != (ref,)
            or block.origin != "native"
            or block.layout != "tree"
            or draft.projected_nodes[ref]["role"] not in MEMBER_ROLES
            or draft.evidence[ref].hidden
            or draft.evidence[ref].incomplete
            or draft.evidence[ref].boundary
        ):
            return None
        if draft.geometric_sources is not None and ref not in draft.geometric_sources.allowed_layout_refs:
            return None
        children = complete_children(draft, ref)
        if children is None or len(children) > 1:
            return None
        members.append(current)
        refs.append(ref)
        seen.add(current)
        if not children:
            if block.children:
                return None
            break
        if len(block.children) != 1:
            return None
        child_key = block.children[0]
        child = draft.blocks[child_key]
        if child.primary_ref != children[0] or not confirmed_edge(draft, ref, children[0]):
            return None
        current = child_key
    controls = tuple(ref for ref in refs if draft.projected_nodes[ref]["role"] in CONTROL_ROLES)
    if len(controls) != 1 or len(members) < 2:
        return None
    target = controls[0]
    target_index = refs.index(target)
    if target_index == 0 or any(draft.projected_nodes[ref]["role"] != "group" for ref in refs[:target_index]):
        return None
    if any(
        draft.projected_nodes[ref]["role"] not in ("group", "staticText", "image") for ref in refs[target_index + 1 :]
    ):
        return None
    if not one_window(draft, tuple(refs)):
        return None
    boxes = {ref: draft.source.geometry.boxes.get(ref) for ref in refs}
    if any(box is None for box in boxes.values()):
        return None
    target_box = boxes[target]
    if any(not close_wrapper(boxes[ref], target_box) for ref in refs[:target_index]) or any(
        not contained_caption(boxes[ref], target_box) for ref in refs[target_index + 1 :]
    ):
        return None
    last = refs[-1]
    node = draft.projected_nodes[last]
    label = (
        last
        if node["role"] == "staticText"
        and any(isinstance(node.get(field), str) and node[field].strip() for field in ("name", "value", "description"))
        else None
    )
    return CompoundControl(
        key,
        target,
        tuple(members),
        tuple(refs),
        label,
        (
            "complete-native-single-child-path",
            "unique-semantic-control",
            "coincident-wrapper-bounds",
            "all-original-action-owners-retained",
        ),
    )


def select_compound_controls(draft: InterfaceDraft) -> tuple[CompoundControl, ...]:
    """Choose non-overlapping outermost visual control compounds.

    Args:
        draft: Exact source-backed blocks immediately after scope indexing. Unknown
            wrapper capabilities remain source facts and do not imply passivity.

    Returns:
        Whole native paths containing one semantic control. Coincident wrappers
        retain separate identities, fields, states and actions; their methods are
        never treated as equivalent to the principal control's methods.
    """
    if not draft.enabled:
        return ()
    plans = []
    used: set[str] = set()
    pending = list(reversed(draft.roots))
    while pending:
        key = pending.pop()
        if key in used:
            continue
        plan = _candidate(draft, key)
        if plan is not None:
            plans.append(plan)
            used.update(plan.members)
        else:
            pending.extend(reversed(draft.blocks[key].children))
    return tuple(plans)
