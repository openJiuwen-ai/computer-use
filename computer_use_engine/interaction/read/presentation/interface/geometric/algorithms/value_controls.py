# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep a labelled readonly value and its adjacent selector in one display field."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.helpers import revise, same_scope
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges, stable_order
from computer_use.interaction.read.presentation.interface.regions.scope import (
    block_sources,
    comparable_members,
    ordered_ancestry,
)


def _readonly_value(block: InterfaceBlock, draft: InterfaceDraft) -> str | None:
    """Require a complete label/value field with an explicitly readonly text control."""
    target, label = block.field_target_ref, block.label_ref
    if (
        block.kind != "field"
        or block.visual_unit
        or target is None
        or label is None
        or len(block.children) != 2
        or any(draft.blocks[key].children for key in block.children)
        or set(block_sources(block.key, draft.blocks)) != {target, label}
    ):
        return None
    node = draft.projected_nodes[target]
    if (
        node["role"] not in ("textField", "textArea")
        or "value" not in node
        or not (node.get("enabled") is False or node.get("readOnly") is True or node.get("textReadOnly") is True)
    ):
        return None
    return target


def _selector(block: InterfaceBlock, draft: InterfaceDraft) -> str | None:
    """Recognize a separate enabled button with a declared click route."""
    ref = block.primary_ref
    if ref is None or block.children:
        return None
    node = draft.projected_nodes[ref]
    if (
        node["role"] not in ("button", "menuButton", "popUpButton")
        or node.get("enabled") is False
        or node.get("subrole") in ("close", "minimize", "zoom", "fullscreen")
        or node.get("capabilities", {}).get("click") != "available"
    ):
        return None
    return ref


def associate_value_controls(draft: InterfaceDraft) -> InterfaceDraft:
    """Extend an existing readonly field with its unique compact adjacent selector.

    Args:
        draft: Source-preserving fields and regional constraints before row planning.

    Returns:
        A draft retaining the displayed value and selector as independent targets in
        one labelled field. Complete common geometry and native order constrain the
        association; editable values, large action buttons and competing selectors
        retain their existing organization.
    """
    if not draft.enabled:
        return draft
    blocks, decisions = dict(draft.blocks), []
    for parent in draft.blocks.values():
        if (
            parent.kind in ("field", "table", "collection", "record")
            or parent.layout != "tree"
            or parent.primary_ref is None
            or ordered_ancestry(parent.primary_ref, draft)
            or draft.evidence[parent.primary_ref].incomplete
            or draft.evidence[parent.primary_ref].hidden
        ):
            continue
        output, index = [], 0
        while index < len(parent.children):
            key = parent.children[index]
            field = draft.blocks[key]
            value = _readonly_value(field, draft)
            following = parent.children[index + 1 : index + 2]
            trigger = _selector(draft.blocks[following[0]], draft) if following else None
            if value is None or trigger is None:
                output.append(key)
                index += 1
                continue
            keys = (key, following[0])
            refs = (*block_sources(key, draft.blocks), trigger)
            measured = comparable_members(keys, draft, draft.blocks)
            value_box, trigger_box = (draft.source.geometry.boxes.get(ref) for ref in (value, trigger))
            node = draft.projected_nodes[trigger]
            captions = {node[name] for name in ("titleRef", "visualLabelRef") if node.get(name)}
            owners = {ref: member for member in keys for ref in block_sources(member, draft.blocks)}
            edges = native_edges(keys, owners, draft.relations)
            other = (
                _selector(draft.blocks[parent.children[index + 2]], draft) if index + 2 < len(parent.children) else None
            )
            other_box = draft.source.geometry.boxes.get(other)
            competing = (
                value_box is not None
                and trigger_box is not None
                and other_box is not None
                and value_box.comparable(other_box)
                and value_box.vertical_overlap(other_box) >= 0.75
                and 0 <= other_box.x - trigger_box.right <= value_box.height
            )
            if (
                measured is None
                or value_box is None
                or trigger_box is None
                or not same_scope(draft, refs)
                or not same_region(draft, refs)
                or any(
                    draft.evidence[ref].incomplete
                    or draft.evidence[ref].hidden
                    or draft.evidence[ref].boundary
                    or ordered_ancestry(ref, draft)
                    for ref in refs
                )
                or captions - {field.label_ref}
                or node.get("incompleteLabelRelation")
                or edges is None
                or stable_order(keys, edges) != keys
                or any(
                    relation.kind in ("flows_to", "flows_from")
                    and set((relation.source, *relation.targets)) & owners.keys()
                    and not set((relation.source, *relation.targets)) <= owners.keys()
                    for relation in draft.relations
                )
                or competing
                or not value_box.comparable(trigger_box)
                or value_box.vertical_overlap(trigger_box) < 0.75
                or not 0 <= trigger_box.x - value_box.right <= value_box.height
                or trigger_box.width > 2 * value_box.height
                or trigger_box.height > 1.5 * value_box.height
            ):
                output.append(key)
                index += 1
                continue
            blocks[key] = replace(
                field,
                children=(*field.children, following[0]),
                layout="fields",
                origin="visual",
                field_target_ref=None,
                member_roles=(*field.member_roles, (trigger, "control")),
            )
            output.append(key)
            decisions.append("geometric-value-control:" + value + ":" + trigger)
            index += 2
        if tuple(output) != parent.children:
            blocks[parent.key] = replace(parent, children=tuple(output))
    return revise(draft, blocks, *decisions)
