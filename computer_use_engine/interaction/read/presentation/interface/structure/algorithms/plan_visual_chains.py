# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose inline parent-child edges already proven to describe one visual unit."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise, same_scope
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft


def _single_atom_cost(draft: InterfaceDraft, ref: str) -> int | None:
    """Estimate one-line cost from typed fields, retaining unsupported forms as blocks."""
    atom = draft.atoms.atoms[ref]
    if atom.coverage.partial or atom.declarations:
        return None
    cost = len(ref) + len(atom.role.label) + 2
    cost += sum(len(state.label) + 2 for state in atom.states)
    for slot in atom.slots:
        if (
            slot.placement == "block"
            or slot.preview is not None
            or isinstance(slot.value, str)
            and any(character in slot.value for character in "\r\n\t")
        ):
            return None
        cost += len(str(slot.value)) + len(slot.label or "") + 4
    cost += sum(len(action.label) + len(action.name) + 24 for action in atom.actions)
    cost += 34 if atom.actions_unavailable else 0
    return cost


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Preserve each target while planning bounded, visually confirmed short chains."""
    if not draft.enabled or draft.atoms is None:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    # Children are classified before ancestors so a chain's length includes its
    # already selected descendant edges instead of independently allowing each pair.
    costs: dict[str, int | None] = {}
    pending = list(reversed(draft.roots))
    order = []
    while pending:
        key = pending.pop()
        order.append(key)
        pending.extend(reversed(blocks[key].children))
    for key in reversed(order):
        block = blocks[key]
        ref = block.primary_ref
        if ref is None:
            costs[key] = None
            continue
        costs[key] = _single_atom_cost(draft, ref)
        if (
            block.layout != "tree"
            or block.kind in ("window", "region", "table", "field", "record")
            or len(block.children) != 1
            or costs[key] is None
        ):
            continue
        child_key = block.children[0]
        child = blocks[child_key]
        target = child.primary_ref
        if (
            target is None
            or child.layout not in ("tree", "chain")
            or child.kind in ("window", "region", "table", "field", "record")
            or draft.projected_nodes[ref].get("visualChainChild") != target
            or tuple(draft.projected_nodes[ref].get("children", ())) != (target,)
            or any(draft.evidence[item].incomplete or draft.evidence[item].hidden for item in (ref, target))
            or not same_scope(draft, (ref, target))
            or costs[child_key] is None
        ):
            continue
        combined = costs[key] + costs[child_key] + 3
        if combined > 280:
            continue
        blocks[key] = replace(block, layout="chain")
        costs[key] = combined
        decisions.append("visual-chain:" + ref + ">" + target)
    return revise(draft, blocks, *decisions)
