# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Adapt complete display units to source-preserving outer row members."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from computer_use.interaction.read.presentation.interface.helpers import same_scope, single_line
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import ordered_ancestry, reading_scope
from computer_use.interaction.read.presentation.spatial.geometry import Box


@dataclass(frozen=True, slots=True)
class RowUnit:
    """One complete row member and the real sources retained inside it.

    Attributes:
        key: Existing display block identity, independent of operation references.
        target_ref: Original atomic member or the confirmed control inside a unit.
        sources: All real sources owned by the complete member, without duplicates.
        box: Comparable bounds of the whole member, including its visible caption.
    """

    key: str
    target_ref: str
    sources: tuple[str, ...]
    box: Box


def inline_unit_refs(key: str, blocks: Mapping[str, InterfaceBlock]) -> tuple[str, ...] | None:
    """Identify a complete atomic, original record-path or control/caption unit.

    Args:
        key: Existing candidate block; no additional composition is inferred.
        blocks: Source-bound block graph used by planning or final validation.

    Returns:
        Original sources in their retained order, including original path and
        passive-wrapper sources. A branching subtree keeps its general layout.
    """
    block = blocks[key]
    if block.primary_ref is not None:
        if block.layout == "record-path" and block.source_refs == (block.primary_ref,) and len(block.children) == 1:
            sources = [block.primary_ref]
            visited = {key}
            current = block.children[0]
            while current not in visited and current in blocks:
                visited.add(current)
                child = blocks[current]
                if child.primary_ref is None or child.source_refs != (child.primary_ref,):
                    return None
                sources.append(child.primary_ref)
                if not child.children:
                    return tuple(sources) if len(set(sources)) == len(sources) else None
                if child.layout != "record-path" or len(child.children) != 1:
                    return None
                current = child.children[0]
            return None
        return (
            (block.primary_ref,)
            if not block.children
            and block.source_refs == (block.primary_ref,)
            and block.kind not in ("region", "window", "table", "collection", "record")
            else None
        )
    target, label = block.field_target_ref, block.label_ref
    roles = dict(block.member_roles)
    if (
        not block.visual_unit
        or block.kind != "field"
        or block.source_refs
        or block.layout not in ("fields", "control-unit")
        or target is None
        or label is None
        or target == label
        or len(roles) != len(block.member_roles)
        or roles.get(target) != "control"
        or roles.get(label) != "label"
        or any(role != "content" for ref, role in roles.items() if ref not in (target, label))
    ):
        return None
    sources: list[str] = []
    visited: set[str] = set()
    pending = list(reversed(block.children))
    while pending:
        child_key = pending.pop()
        if child_key in visited or child_key not in blocks:
            return None
        visited.add(child_key)
        child = blocks[child_key]
        if child.primary_ref is None or child.source_refs != (child.primary_ref,):
            return None
        sources.extend(child.source_refs)
        pending.extend(reversed(child.children))
    return (
        tuple(sources) if len(set(sources)) == len(sources) and set(sources) == set(dict(block.member_roles)) else None
    )


def prepared_unit_rows(
    parent: InterfaceBlock, draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]
) -> tuple[tuple[str, ...], ...] | None:
    """Map complete prepared source rows to their current whole display units.

    Args:
        parent: Existing owner of row candidates and the currently retained units.
        draft: Immutable source candidates and native relationships.
        blocks: Current block graph, including already-confirmed control units.

    Returns:
        Complete rows in the parent's current constrained order, or None when a
        candidate splits a unit, leaves a source unresolved, or interleaves rows.
        Parents containing only atomic children retain the existing record route.
        Geometry and native scope eligibility remain the row planner's responsibility.
    """
    if parent.primary_ref is None or not any(blocks[key].visual_unit for key in parent.children):
        return None
    groups = tuple(draft.projected_nodes[parent.primary_ref].get("layoutGroups", ()))
    if not groups or any(group.get("kind") != "row" for group in groups):
        return None
    candidates = {key: inline_unit_refs(key, blocks) for key in parent.children}
    if any(refs is None for refs in candidates.values()):
        return None
    sources = {key: refs for key, refs in candidates.items() if refs is not None}
    owned = tuple(ref for refs in sources.values() for ref in refs)
    if len(set(owned)) != len(owned):
        return None
    source_rows: dict[str, int] = {}
    for index, group in enumerate(groups):
        members = tuple(group.get("members", ()))
        if not members:
            return None
        for ref in members:
            if not isinstance(ref, str) or ref in source_rows:
                return None
            source_rows[ref] = index
    if set(source_rows) != set(owned):
        return None
    rows: list[tuple[str, ...]] = []
    current: list[str] = []
    visited: set[int] = set()
    previous: int | None = None
    for key in parent.children:
        indices = {source_rows[ref] for ref in sources[key]}
        if len(indices) != 1:
            return None
        index = next(iter(indices))
        if index != previous:
            if index in visited:
                return None
            visited.add(index)
            if current:
                rows.append(tuple(current))
            current = []
            previous = index
        current.append(key)
    if current:
        rows.append(tuple(current))
    return tuple(rows)


def row_unit(key: str, draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> RowUnit | None:
    """Measure an existing complete unit without changing its input target.

    Atomic leaves retain their row eligibility rules. A source-verified record
    path can be measured within its logical collection while keeping every
    original edge. Every composed source requires complete single-line content
    and comparable bounds in the same native reading scope.
    """
    refs = inline_unit_refs(key, blocks)
    if refs is None:
        return None
    if (
        not same_scope(draft, refs)
        or len({reading_scope(ref, draft) for ref in refs}) != 1
        or draft.geometric_sources is not None
        and not set(refs) <= draft.geometric_sources.allowed_layout_refs
    ):
        return None
    boxes = []
    path = blocks[key].layout == "record-path"
    for ref in refs:
        node, evidence = draft.projected_nodes[ref], draft.evidence[ref]
        if (
            (evidence.boundary and not path)
            or evidence.incomplete
            or evidence.hidden
            or ordered_ancestry(ref, draft)
            and not path
            or not single_line(node)
            or node.get("subrole") in ("close", "minimize", "zoom", "fullscreen")
            or node["role"] in ("closeButton", "minimizeButton", "zoomButton")
        ):
            return None
        box = draft.source.geometry.boxes.get(ref)
        if box is None:
            return None
        boxes.append(box)
        if len(refs) > 1 and draft.atoms is not None:
            atom = draft.atoms.atoms.get(ref)
            if (
                atom is None
                or atom.coverage.partial
                or atom.declarations
                or any(
                    slot.placement == "block"
                    or isinstance(slot.value, str)
                    and any(char in slot.value for char in ("\r", "\n", "\t", "|"))
                    for slot in atom.slots
                )
            ):
                return None
    if len({box.space for box in boxes}) != 1:
        return None
    left, top = min(box.x for box in boxes), min(box.y for box in boxes)
    bounds = Box(
        boxes[0].space, left, top, max(box.right for box in boxes) - left, max(box.bottom for box in boxes) - top
    )
    target = blocks[key].primary_ref or blocks[key].field_target_ref
    assert target is not None
    return RowUnit(key, target, refs, bounds)
