# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain one visual control as a unit with its separately bound caption source."""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping

from computer_use.interaction.read.presentation.interface.composition.placement import member_span
from computer_use.interaction.read.presentation.interface.geometric.algorithms.structure import _label_evidence
from computer_use.interaction.read.presentation.interface.geometric.frontier import transparent_group
from computer_use.interaction.read.presentation.interface.helpers import revise, same_scope, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges, stable_order
from computer_use.interaction.read.presentation.interface.regions.scope import (
    ORDERED_ROLES,
    block_sources,
    ordered_ancestry,
    reading_scope,
)
from computer_use.interaction.read.presentation.spatial.association import relation
from computer_use.interaction.read.presentation.spatial.policy import CONTAINED_CAPTIONS, form_control


def _local_boundary(ref: str, draft: InterfaceDraft) -> str | None:
    """Find the first original reading boundary without crossing uncertain ancestry."""
    sources = draft.geometric_sources
    nodes = sources.semantic_nodes if sources is not None else draft.source.native_nodes
    current: str | None = ref
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            return None
        # Complete target/caption facts are checked separately. An ancestor's
        # unread siblings do not invalidate this already-confirmed local path.
        if node.get("visible") is False or node.get("showing") is False or node.get("offscreen") is True:
            return None
        if node["role"] in ORDERED_ROLES | {"window", "sheet", "scrollArea"}:
            return current
        if sources is not None:
            report = sources.parent_reports.get(current)
            if report is None or report.status in ("unknown", "conflict"):
                return None
            current = sources.original_parent(current)
        else:
            current = draft.source.parents.get(current)
    return None


def _same_visual_control(target: str, label: str, draft: InterfaceDraft) -> bool:
    """Recheck an established visual label inside its exact source and geometry scope."""
    if label not in draft.projected_nodes or target == label:
        return False
    refs = (target, label)
    sources = draft.geometric_sources
    if sources is not None and not set(refs) <= sources.allowed_layout_refs:
        return False
    control, caption = (draft.projected_nodes[ref] for ref in refs)
    selection_row = control["role"] in ("row", "cell") and (
        control.get("selectable") is True or control.get("selected") is True
    )
    if (
        control["role"] not in CONTAINED_CAPTIONS
        and not form_control(control)
        and not selection_row
        or caption["role"] != "staticText"
        or caption.get("children")
        or not any(
            isinstance(caption.get(key), str) and caption[key].strip() for key in ("name", "value", "description")
        )
        or any(
            (draft.evidence[ref].boundary and not (selection_row and ref == target))
            or draft.evidence[ref].hidden
            or draft.evidence[ref].incomplete
            for ref in refs
        )
        or not same_scope(draft, refs)
    ):
        return False
    target_scope, label_scope = (reading_scope(ref, draft) for ref in refs)
    if target_scope != label_scope or any(scope.startswith("unresolved:") for scope in target_scope):
        return False
    boundary = _local_boundary(target, draft)
    label_boundary = _local_boundary(label, draft)
    contained = label in _contained_caption_path(target, draft)
    if boundary is None or boundary != label_boundary and not (selection_row and contained):
        return False
    _, conflicts = _label_evidence(label, (target,), draft)
    if conflicts:
        return False
    target_box, label_box = (draft.source.geometry.boxes.get(ref) for ref in refs)
    if target_box is None or label_box is None:
        return False
    placement = relation(label_box, target_box, control["role"], field_like=form_control(control))
    return placement == "inside" or (placement == "right" and control["role"] in ("checkBox", "radioButton", "switch"))


def _contained_caption_path(target: str, draft: InterfaceDraft) -> tuple[str, ...]:
    """Follow a complete one-child control path through passive implementation nodes."""
    source = draft.geometric_sources
    path = [target]
    visited = {target}
    while True:
        ref = path[-1]
        node = draft.projected_nodes.get(ref)
        original = draft.source.native_nodes.get(ref)
        if node is None or original is None:
            return ()
        children = tuple(node.get("children", ()))
        original_children = tuple(child["ref"] for child in original.get("children", ()))
        if len(children) != 1 or children != original_children:
            return ()
        child = children[0]
        if (
            child in visited
            or child not in draft.projected_nodes
            or draft.source.parents.get(child) != ref
            or draft.projected_parents.get(child) != ref
        ):
            return ()
        if source is not None:
            report = source.parent_reports.get(child)
            if report is None or report.parent != ref or report.status not in ("confirmed", "semantic"):
                return ()
        path.append(child)
        visited.add(child)
        following = draft.projected_nodes[child]
        if following["role"] == "staticText" and not following.get("children"):
            return tuple(path)
        if not transparent_group(child, draft):
            return ()


def _duplicate_child_caption(target: str, draft: InterfaceDraft) -> str | None:
    """Find a complete caption copy along one proved original control path.

    Passive intermediate groups and the caption keep their real source references.
    Content planning selects a caption reference only when its stricter field
    ownership evidence is available.
    """
    node = draft.projected_nodes[target]
    if (
        node["role"] not in ("button", "link")
        or node.get("capabilities", {}).get("click") != "available"
        or draft.atoms is None
    ):
        return None
    path = _contained_caption_path(target, draft)
    if not path:
        return None
    child = path[-1]
    source = draft.geometric_sources
    if source is not None:
        boundary = _local_boundary(target, draft)
        boundary_token = source.ref_to_native.get(boundary)
        for ref in (target, child):
            native = source.native_node(ref)
            if native is not None and any(
                relation.targets and (boundary_token is None or relation.targets != (boundary_token,))
                for relation in native.relations.values()
                if relation.kind == "window"
            ):
                return None
    target_atom, child_atom = draft.atoms.atoms[target], draft.atoms.atoms[child]
    captions = tuple(slot for slot in target_atom.slots if slot.placement == "caption")
    if (
        len(captions) != 1
        or not isinstance(captions[0].value, str)
        or not captions[0].value
        or len(child_atom.slots) != 1
    ):
        return None
    slot = child_atom.slots[0]
    if (
        slot.key not in ("name", "description", "value")
        or slot.format != "text"
        or slot.value != captions[0].value
        or slot.protection
        or any(character in slot.value for character in ("\r", "\n", "\t"))
    ):
        return None
    return child if _same_visual_control(target, child, draft) else None


def _unit_order(
    children: tuple[str, ...], target: str, draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]
) -> tuple[str, ...]:
    """Give an unordered control unit a stable target-first presentation identity.

    Args:
        children: Complete sibling blocks containing the confirmed control and caption.
        target: Original control reference, independent of its enumeration position.
        draft: Captured facts and native precedence for this layout.
        blocks: Source-preserving blocks, including any transparent wrappers.

    Returns:
        Canonical local members constrained by complete native flow. Logical sequences,
        incomplete flow and relations extending outside the unit retain captured order.
    """
    owners = {ref: key for key in children for ref in block_sources(key, blocks)}
    if len(children) < 2 or any(ordered_ancestry(ref, draft) for ref in owners):
        return children
    for edge in draft.relations:
        if edge.kind in ("flows_to", "flows_from"):
            endpoints = frozenset((edge.source, *edge.targets))
            if endpoints.intersection(owners) and not endpoints <= owners.keys():
                return children
    edges = native_edges(children, owners, draft.relations)
    if edges is None:
        return children
    canonical = tuple(sorted(children, key=lambda key: (key != owners[target], key)))
    return stable_order(canonical, edges) or children


def compose_control_units(draft: InterfaceDraft) -> InterfaceDraft:
    """Group a control and its proved inside/right caption without moving source facts.

    Args:
        draft: Exact source-backed blocks with previously established visualLabelRef
            candidates or a directly contained duplicate caption. Outside left/above
            labels remain available to form layout.

    Returns:
        A draft with explicit visual units. Each unit retains the original control
        as its input target and preserves every original caption/reference block;
        final content planning separately decides whether caption text can compose.
    """
    if not draft.enabled:
        return draft
    labels: dict[str, str] = {}
    users: dict[str, list[str]] = {}
    for ref, node in draft.projected_nodes.items():
        label = node.get("visualLabelRef")
        if not isinstance(label, str):
            label = _duplicate_child_caption(ref, draft)
        if isinstance(label, str):
            labels[ref] = label
            users.setdefault(label, []).append(ref)
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    used: set[str] = set()
    for target in draft.order:
        label = labels.get(target)
        if (
            not isinstance(label, str)
            or users.get(label) != [target]
            or used.intersection((target, label))
            or not _same_visual_control(target, label, draft)
        ):
            continue
        span = member_span((target, label), draft, blocks)
        if span is None or blocks[span.parent].kind == "field":
            continue
        parent = blocks[span.parent]
        children = _unit_order(span.children, target, draft, blocks)
        key = synthetic_key("control-unit", span.parent, children)
        members = tuple(ref for child in children for ref in block_sources(child, blocks))
        blocks[key] = InterfaceBlock(
            key,
            "field",
            None,
            (),
            children,
            layout="fields",
            origin="visual",
            label_ref=label,
            field_target_ref=target,
            member_roles=(
                (target, "control"),
                (label, "label"),
                *((ref, "content") for ref in members if ref not in (target, label)),
            ),
            visual_unit=True,
        )
        blocks[parent.key] = replace(
            parent, children=(*parent.children[: span.start], key, *parent.children[span.stop :])
        )
        used.update((target, label))
        decisions.append("geometric-control-unit:" + target)
    return revise(draft, blocks, *decisions)
