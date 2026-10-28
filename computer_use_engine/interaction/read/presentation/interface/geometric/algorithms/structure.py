# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize proved local label/control rows as nested visual fields."""

from __future__ import annotations

from dataclasses import dataclass, replace
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region

from computer_use.interaction.read.presentation.interface.geometric.algorithms.rows import local_parent, row_geometry
from computer_use.interaction.read.presentation.interface.helpers import (
    CONTROL_ROLES,
    FIELD_ROLES,
    revise,
    synthetic_key,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.spatial.alignment import (
    AlignmentAnalysis,
    LayoutItem,
    analyze_alignment,
    shared_anchors,
)
from computer_use.interaction.read.presentation.spatial.association import referenceable_caption


@dataclass(frozen=True)
class _Subfield:
    """One complete visual row and the original label and control sources in it."""

    key: str
    label: str
    controls: tuple[str, ...]
    content: tuple[str, ...]
    confirmed: bool


def _control(ref: str, draft: InterfaceDraft) -> bool:
    node = draft.projected_nodes[ref]
    return node["role"] in CONTROL_ROLES | FIELD_ROLES or any(
        node.get("capabilities", {}).get(key) == "available" for key in ("click", "typeText", "replaceText")
    )


def _label_evidence(label: str, targets: tuple[str, ...], draft: InterfaceDraft) -> tuple[bool, bool]:
    """Return explicit support and conflicting/incomplete caption evidence."""
    confirmed = False
    conflicts = False
    selected = set(targets)
    for ref in targets:
        node = draft.projected_nodes[ref]
        captions = tuple(node[name] for name in ("titleRef", "visualLabelRef") if isinstance(node.get(name), str))
        confirmed |= label in captions
        conflicts |= bool(set(captions) - {label}) or bool(node.get("incompleteLabelRelation"))
    node = draft.projected_nodes[label]
    linked = set(node.get("labelTargets", ()))
    confirmed |= bool(linked & selected)
    conflicts |= bool(linked - selected) or bool(node.get("incompleteLabelRelation"))
    for relation in draft.relations:
        if relation.kind not in ("labelled_by", "labels"):
            continue
        pairs = (
            ((target, relation.source) for target in relation.targets)
            if relation.kind == "labels"
            else ((relation.source, caption) for caption in relation.targets)
        )
        for target, caption in pairs:
            if target not in selected and caption != label:
                continue
            if not relation.complete or not relation.confirmed:
                conflicts = True
            elif target in selected and caption == label:
                confirmed = True
            else:
                conflicts = True
        if not relation.targets and (relation.source in selected or relation.source == label):
            conflicts |= not relation.complete or not relation.confirmed
    return confirmed, conflicts


def _candidate(key: str, draft: InterfaceDraft) -> _Subfield | None:
    row = draft.blocks[key]
    if (
        row.layout != "visual-row"
        or row.primary_ref is not None
        or row.source_refs
        or len(row.children) < 2
        or not row_geometry(row.children, draft, draft.blocks)
    ):
        return None
    refs = tuple(draft.blocks[child].primary_ref for child in row.children)
    if any(ref is None for ref in refs):
        return None
    members = tuple(ref for ref in refs if ref is not None)
    label = members[0]
    node = draft.projected_nodes[label]
    controls = tuple(ref for ref in members[1:] if _control(ref, draft))
    if not controls or node["role"] not in ("staticText", "heading"):
        return None
    content = tuple(ref for ref in members[1:] if ref not in controls)
    if any(draft.projected_nodes[ref]["role"] != "image" or draft.evidence[ref].independent for ref in content):
        return None
    confirmed, conflicts = _label_evidence(label, controls, draft)
    if conflicts or (not confirmed and not referenceable_caption(node)):
        return None
    # A proven label may have its own action; its target and facts remain local.
    if not any(isinstance(node.get(name), str) and node[name].strip() for name in ("name", "value", "description")):
        return None
    return _Subfield(key, label, controls, content, confirmed)


def _repeated(
    candidate: _Subfield, others: tuple[_Subfield, ...], alignment: AlignmentAnalysis, draft: InterfaceDraft
) -> bool:
    """Require common label and control anchors across two eligible field rows."""
    for other in others:
        if other.key == candidate.key or not same_region(
            draft, (candidate.label, *candidate.controls, other.label, *other.controls)
        ):
            continue
        if shared_anchors(alignment, (candidate.label, other.label)) and shared_anchors(
            alignment, (candidate.controls[-1], other.controls[-1])
        ):
            return True
    return False


def _local_alignment(parent: InterfaceBlock, draft: InterfaceDraft) -> dict[str, AlignmentAnalysis]:
    """Analyze existing local units, retaining other visible rows as anchor barriers."""
    units: dict[str, list[LayoutItem]] = {}
    pending = list(parent.children)
    seen: set[str] = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        block = draft.blocks[key]
        if block.layout == "visual-row" and block.primary_ref is None:
            pending.extend(block.children)
        elif not block.children and block.primary_ref is not None:
            ref = block.primary_ref
            box = draft.source.geometry.boxes.get(ref)
            if box is not None:
                units.setdefault(box.space, []).append(LayoutItem(ref, box))
    return {space: analyze_alignment(items) for space, items in units.items()}


def organize_subfields(draft: InterfaceDraft) -> InterfaceDraft:
    """Turn source-supported rows into fields, including fields inside other fields.

    A native/previously proved unique label relation can support one row. Visual
    association requires at least two aligned label/control rows in the same
    local parent. Every original label, value, description and action remains on
    its original source; no displayed field becomes a synthetic input target.

    Args:
        draft: Current source-complete block graph after geometric row grouping.

    Returns:
        A draft whose proved subfields have a label and independently bound
        control members. Unproved ordinary rows retain their visual-row form.
    """
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    for parent in draft.blocks.values():
        if not local_parent(parent, draft, minimum_children=1):
            continue
        candidates = tuple(candidate for key in parent.children if (candidate := _candidate(key, draft)) is not None)
        if not candidates:
            continue
        alignments = _local_alignment(parent, draft)
        for candidate in candidates:
            alignment = alignments[draft.source.geometry.boxes[candidate.label].space]
            if not candidate.confirmed and not _repeated(candidate, candidates, alignment, draft):
                continue
            row = blocks[candidate.key]
            roles = ((candidate.label, "label"),)
            roles += tuple((ref, "control") for ref in candidate.controls)
            roles += tuple((ref, "content") for ref in candidate.content)
            children = row.children
            if len(children) > 2:
                content_key = synthetic_key("field-members", row.key, children[1:])
                blocks[content_key] = InterfaceBlock(
                    content_key, "content", None, (), children[1:], layout="visual-row", origin="visual"
                )
                children = (children[0], content_key)
            blocks[candidate.key] = replace(
                row,
                kind="field",
                layout="field-inline",
                children=children,
                label_ref=candidate.label,
                member_roles=roles,
            )
            decisions.append("geometric-subfield:" + candidate.label)
    return revise(draft, blocks, *decisions)
