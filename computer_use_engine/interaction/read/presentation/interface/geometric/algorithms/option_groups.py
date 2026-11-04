# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep an evidenced radio question beside all of its independently bound options."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.algorithms.rows import local_parent, row_geometry
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.geometric.units_support import inline_unit_refs
from computer_use.interaction.read.presentation.interface.helpers import revise, same_scope, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges, stable_order
from computer_use.interaction.read.presentation.interface.regions.scope import (
    block_bounds,
    block_sources,
    ordered_ancestry,
    reading_scope,
)
from computer_use.interaction.read.presentation.spatial.association import referenceable_caption
from computer_use.interaction.read.presentation.spatial.geometry import Box


def _radio_targets(key: str, draft: InterfaceDraft) -> tuple[str, ...] | None:
    """Read complete option units or a preselected row containing only options."""
    block = draft.blocks[key]
    target = block.field_target_ref if block.visual_unit else block.primary_ref
    if target is not None and draft.projected_nodes[target]["role"] == "radioButton":
        return (target,) if inline_unit_refs(key, draft.blocks) is not None else None
    if block.primary_ref is None and not block.source_refs and block.layout == "visual-row" and block.children:
        members = tuple(_radio_targets(child, draft) for child in block.children)
        if all(group is not None for group in members):
            return tuple(ref for group in members if group is not None for ref in group)
    return None


def _label_support(label: str, targets: tuple[str, ...], draft: InterfaceDraft) -> tuple[bool, bool]:
    """Separate complete shared native labels from contradictory label reports."""
    selected = frozenset(targets)
    confirmed: set[str] = set()
    for ref in targets:
        node = draft.projected_nodes[ref]
        if node.get("incompleteLabelRelation"):
            return False, True
        title = node.get("titleRef")
        if isinstance(title, str):
            if title != label:
                return False, True
            confirmed.add(ref)
        visual = node.get("visualLabelRef")
        if isinstance(visual, str) and visual != label:
            return False, True
    caption = draft.projected_nodes[label]
    linked = frozenset(caption.get("labelTargets", ()))
    if caption.get("incompleteLabelRelation") or linked - selected:
        return False, True
    confirmed.update(linked)
    for relation in draft.relations:
        if relation.kind not in ("labelled_by", "labels"):
            continue
        pairs = (
            ((target, relation.source) for target in relation.targets)
            if relation.kind == "labels"
            else ((relation.source, caption) for caption in relation.targets)
        )
        for target, caption_ref in pairs:
            if target not in selected and caption_ref != label:
                continue
            if not relation.complete or not relation.confirmed or target not in selected or caption_ref != label:
                return False, True
            confirmed.add(target)
        if not relation.targets and (relation.source == label or relation.source in selected):
            if not relation.complete or not relation.confirmed:
                return False, True
    return confirmed == selected, bool(confirmed and confirmed != selected)


def _question_start(key: str, draft: InterfaceDraft) -> tuple[str, tuple[str, ...]] | None:
    """Find a real question or a complete visual label paired with its first option."""
    block = draft.blocks[key]
    if block.primary_ref is not None:
        return key, ()
    target, label = block.field_target_ref, block.label_ref
    if (
        block.kind != "field"
        or block.visual_unit
        or block.origin != "visual"
        or block.source_refs
        or target is None
        or label is None
        or draft.projected_nodes[target]["role"] != "radioButton"
        or len(block.children) != 2
    ):
        return None
    members = {draft.blocks[child].primary_ref: child for child in block.children if not draft.blocks[child].children}
    if set(members) != {target, label} or dict(block.member_roles) != {target: "control", label: "label"}:
        return None
    return members[label], (members[target],)


def _nearby_question(label: Box, options: tuple[Box, ...]) -> bool:
    """Accept a compact question above its option band or beside its first row."""
    if any(not label.comparable(box) for box in options):
        return False
    scale = min(label.height, *(box.height for box in options))
    left, top = min(box.x for box in options), min(box.y for box in options)
    if scale <= 0:
        return False
    # Every option must belong to the same compact row/column group. A large
    # gap marks a separate question even when both groups share a distant title.
    rows = sorted(options, key=lambda box: (box.y, box.x))
    if any(after.y - before.bottom > 1.5 * scale for before, after in zip(rows, rows[1:])):
        return False
    if any(
        before.vertical_overlap(after) >= 0.6 and after.x - before.right > 3 * scale
        for before, after in zip(rows, rows[1:])
    ):
        return False
    above = 0 <= top - label.bottom <= 1.5 * scale and abs(label.x - left) <= 0.75 * scale
    beside = (
        0 <= left - label.right <= 2 * scale and label.vertical_overlap(rows[0]) >= 0.6 and label.y <= top + 0.5 * scale
    )
    return above or beside


def _safe_group(label_key: str, children: tuple[str, ...], targets: tuple[str, ...], draft: InterfaceDraft) -> bool:
    """Require one complete scope and a confirmed or compact visual question."""
    label = draft.blocks[label_key].primary_ref
    if label is None or draft.blocks[label_key].children:
        return False
    refs = tuple(ref for key in (label_key, *children) for ref in block_sources(key, draft.blocks))
    if (
        len(refs) != len(set(refs))
        or not same_scope(draft, refs)
        or not same_region(draft, refs)
        or len({reading_scope(ref, draft) for ref in refs}) != 1
        or any(
            draft.evidence[ref].boundary
            or draft.evidence[ref].hidden
            or draft.evidence[ref].incomplete
            or ordered_ancestry(ref, draft)
            for ref in refs
        )
    ):
        return False
    selected = frozenset(refs)
    if any(
        relation.kind in ("flows_to", "flows_from")
        and selected.intersection((relation.source, *relation.targets))
        and (
            not relation.complete
            or not relation.confirmed
            or not frozenset((relation.source, *relation.targets)) <= selected
        )
        for relation in draft.relations
    ):
        return False
    keys = (label_key, *children)
    owners = {ref: key for key in keys for ref in block_sources(key, draft.blocks)}
    edges = native_edges(keys, owners, draft.relations)
    if edges is None or stable_order(keys, edges) != keys:
        return False
    confirmed, conflict = _label_support(label, targets, draft)
    if conflict:
        return False
    node = draft.projected_nodes[label]
    if not any(isinstance(node.get(field), str) and node[field].strip() for field in ("name", "value", "description")):
        return False
    if confirmed:
        return node["role"] in ("staticText", "heading")
    if not referenceable_caption(node):
        return False
    label_box = block_bounds(label_key, draft, draft.blocks)
    boxes = tuple(draft.source.geometry.boxes.get(ref) for ref in targets)
    return (
        label_box is not None
        and all(box is not None for box in boxes)
        and _nearby_question(label_box, tuple(box for box in boxes if box is not None))
    )


def group_radio_options(draft: InterfaceDraft) -> InterfaceDraft:
    """Place a proved question and its complete radio run in one display field.

    Args:
        draft: Complete blocks after row and field organization, with original
            labels, native precedence and immutable source geometry.

    Returns:
        A detached block graph containing question fields with independently bound
        options. Native radio groups retain their existing tree and identity. Each
        field reuses the real question text and all selected states remain on the
        original options; grouping introduces no executable target.
    """
    if not draft.enabled:
        return draft
    blocks, decisions = dict(draft.blocks), []
    for parent in draft.blocks.values():
        if not local_parent(parent, draft):
            continue
        children = parent.children
        output: list[str] = []
        index = 0
        while index < len(children):
            start_key = children[index]
            question = _question_start(start_key, draft)
            if question is None:
                output.append(start_key)
                index += 1
                continue
            label_key, leading = question
            label = draft.blocks[label_key].primary_ref
            stop, options = index + 1, [ref for key in leading for ref in _radio_targets(key, draft) or ()]
            while stop < len(children):
                targets = _radio_targets(children[stop], draft)
                if targets is None:
                    break
                options.extend(targets)
                stop += 1
            members = (*leading, *children[index + 1 : stop])
            if (
                label is None
                or label == parent.label_ref
                or len(options) < 2
                or not _safe_group(label_key, members, tuple(options), draft)
            ):
                output.append(start_key)
                index += 1
                continue
            key = synthetic_key("radio-question", parent.key, (label_key, *members))
            roles = ((label, "label"), *((ref, "control") for ref in options))
            content = members
            if len(members) >= 2 and row_geometry(members, draft, draft.blocks):
                row_key = synthetic_key("radio-options", key, members)
                blocks[row_key] = InterfaceBlock(
                    row_key, "content", None, (), members, layout="visual-row", origin="visual"
                )
                content = (row_key,)
            blocks[key] = InterfaceBlock(
                key,
                "field",
                None,
                (),
                (label_key, *content),
                layout="fields",
                origin="visual",
                label_ref=label,
                member_roles=roles,
            )
            if start_key != label_key:
                blocks.pop(start_key)
            output.append(key)
            index = stop
            decisions.append("geometric-radio-question:" + label)
        if tuple(output) != children:
            blocks[parent.key] = replace(parent, children=tuple(output))
    return revise(draft, blocks, *decisions)
