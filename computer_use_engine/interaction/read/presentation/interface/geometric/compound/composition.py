# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Place a principal control beside its original, independently executable wrappers."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.compound.planning import select_compound_controls
from computer_use.interaction.read.presentation.interface.helpers import revise, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft


def compose_compound_controls(draft: InterfaceDraft) -> InterfaceDraft:
    """Flatten proved visual control paths while keeping every original atom.

    The principal control is the first displayed member. Each enclosing group and
    contained caption remains a separately referenced auxiliary source. Only block
    containment changes; projected/native parents, fields and actions stay intact.
    """
    plans = select_compound_controls(draft)
    if not plans:
        return draft
    blocks = dict(draft.blocks)
    parents = {child: block.key for block in draft.blocks.values() for child in block.children}
    roots = list(draft.roots)
    decisions = []
    for plan in plans:
        key = synthetic_key("compound-control", plan.outer_key, plan.member_refs)
        target_key = next(member for member in plan.members if blocks[member].primary_ref == plan.target_ref)
        ordered = (target_key, *(member for member in plan.members if member != target_key))
        for member in plan.members:
            blocks[member] = replace(blocks[member], children=())
        roles = tuple(
            (ref, "control" if ref == plan.target_ref else "label" if ref == plan.label_ref else "content")
            for ref in plan.member_refs
        )
        blocks[key] = InterfaceBlock(
            key,
            "field",
            None,
            (),
            ordered,
            layout="compound-control",
            origin="visual",
            label_ref=plan.label_ref,
            field_target_ref=plan.target_ref,
            member_roles=roles,
            visual_unit=True,
        )
        parent_key = parents.get(plan.outer_key)
        if parent_key is None:
            roots[roots.index(plan.outer_key)] = key
        else:
            parent = blocks[parent_key]
            blocks[parent_key] = replace(
                parent, children=tuple(key if child == plan.outer_key else child for child in parent.children)
            )
        decisions.append("geometric-compound-control:" + plan.target_ref)
    return replace(revise(draft, blocks, *decisions), roots=tuple(roots))
