# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan incremental delivery from immutable display facts and containment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.delta import DeltaPlan, TreeDelta
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import PreparedNode
    from computer_use.protocol.records import Record


@dataclass
class _TreeComparison:
    before: DisplayTree
    after: DisplayTree
    changes: dict[str, str] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    context: set[str] = field(default_factory=set)
    expanded: set[str] = field(default_factory=set)


_STATE_FIELDS = frozenset(
    ("enabled", "selected", "focused", "busy", "toggleState", "expanded", "disclosing", "visible", "showing", "modal")
)


def _content(node: PreparedNode) -> dict[str, object]:
    return {key: value for key, value in node.fields.items() if key not in _STATE_FIELDS}


def _related(node: PreparedNode) -> tuple[str, ...]:
    fields = node.fields
    return tuple(
        dict.fromkeys((*fields.get("labelTargets", ()), *fields.get("_contextRefs", ()), *fields.get("related", ())))
    )


def _compatible(
    before: DisplayTree, after: DisplayTree, previous_refs: Mapping[str, object], current_refs: Mapping[str, object]
) -> bool:
    """Require compatible presentation versions, scope, bindings and survivor order."""
    if before.scope != after.scope or before.indent != after.indent or before.details != after.details:
        return False
    if (
        before.prepared.request.compact != after.prepared.request.compact
        or before.prepared.request.public_api != after.prepared.request.public_api
        or before.prepared.snapshot.root_contexts != after.prepared.snapshot.root_contexts
    ):
        return False
    if any(root.failure for root in (*before.prepared.roots, *after.prepared.roots)):
        return False
    previous_versions = {
        root.root_ref: root.interface.format_version if root.interface else None for root in before.prepared.roots
    }
    current_versions = {
        root.root_ref: root.interface.format_version if root.interface else None for root in after.prepared.roots
    }
    if set(previous_versions.values()) != set(current_versions.values()) or any(
        previous_versions[ref] != current_versions[ref] for ref in previous_versions.keys() & current_versions.keys()
    ):
        return False
    for tree, bindings in ((before, previous_refs), (after, current_refs)):
        seen: dict[str, PreparedNode] = {}
        for node in tree.nodes:
            if node.ref in seen or node.ref not in bindings:
                return False
            if node.parent is not None and (node.parent not in seen or seen[node.parent].depth + 1 != node.depth):
                return False
            seen[node.ref] = node
    old, new = {node.ref: node for node in before.nodes}, {node.ref: node for node in after.nodes}
    return [node.ref for node in before.nodes if node.ref in new] == [
        node.ref for node in after.nodes if node.ref in old
    ] and all(node.parent == old[node.ref].parent for node in after.nodes if node.ref in old)


def _compare(before: DisplayTree, after: DisplayTree) -> _TreeComparison:
    old, new = {node.ref: node for node in before.nodes}, {node.ref: node for node in after.nodes}
    delta = _TreeComparison(
        before,
        after,
        {node.ref: "~" if node.ref in old else "+" for node in after.nodes if old.get(node.ref) != node},
        tuple(node.ref for node in before.nodes if node.ref not in new),
    )
    children: dict[str, list[str]] = {}
    for node in after.nodes:
        if node.parent is not None:
            children.setdefault(node.parent, []).append(node.ref)
        facts = node.fields
        matches_facts = (
            True
            if (
                facts.get("partialReason") is not None
                or facts.get("childOmissions")
                or "nextChildOffset" in facts
                or facts.get("childrenRead", 0) < facts.get("childCount", 0)
            )
            else False
        )
        if matches_facts:
            delta.context.add(node.ref)
    for ref in delta.changes:
        if ref in old and new[ref].parent is not None and _content(old[ref]) != _content(new[ref]):
            pending = list(children.get(ref, ()))
            while pending:
                child = pending.pop()
                delta.context.add(child)
                delta.expanded.add(child)
                pending.extend(children.get(child, ()))
    for ref in (*delta.changes, *delta.context):
        parent = new[ref].parent
        while parent is not None:
            delta.context.add(parent)
            parent = new[parent].parent
    for ref in delta.missing:
        parent = old[ref].parent
        while parent is not None:
            if parent in new:
                delta.context.add(parent)
            parent = old[parent].parent
    return delta


def _related_context(deltas: list[_TreeComparison]) -> None:
    indexes = [{node.ref: node for node in delta.after.nodes} for delta in deltas]
    locations: dict[str, list[int]] = {}
    for index, nodes in enumerate(indexes):
        for ref in nodes:
            locations.setdefault(ref, []).append(index)
    relationships: list[dict[str, tuple[str, ...]]] = []
    for nodes in indexes:
        children: dict[str, list[PreparedNode]] = {}
        relations = {ref: list(_related(node)) for ref, node in nodes.items()}
        for node in nodes.values():
            if node.parent is not None:
                children.setdefault(node.parent, []).append(node)
            for target in node.fields.get("labelTargets", ()):
                relations.setdefault(target, []).append(node.ref)
        for ref, members in children.items():
            parent = nodes[ref].fields
            matches_parent = (
                True
                if (
                    parent["role"] in ("group", "cell")
                    and not parent.get("name")
                    and not parent.get("description")
                    and len(members) > 1
                    and any(node.fields["role"] in ("staticText", "heading") for node in members)
                )
                else False
            )
            if matches_parent:
                relations[ref].extend(node.ref for node in members)
        relationships.append({ref: tuple(dict.fromkeys(items)) for ref, items in relations.items()})
    pending = [(index, ref) for index, delta in enumerate(deltas) for ref in (*delta.changes, *delta.context)]
    visited: set[tuple[int, str]] = set()
    while pending:
        index, ref = pending.pop()
        if (index, ref) in visited:
            continue
        visited.add((index, ref))
        for related in relationships[index].get(ref, ()):
            candidates = [index] if related in indexes[index] else locations.get(related, [])
            if len(candidates) != 1:
                continue
            target = candidates[0]
            ancestor: str | None = related
            while ancestor is not None:
                if ancestor not in deltas[target].context:
                    deltas[target].context.add(ancestor)
                    pending.append((target, ancestor))
                ancestor = indexes[target][ancestor].parent


def plan_document_diff(
    before: DisplayDocument | None,
    after: DisplayDocument | None,
    previous_refs: Mapping[str, object],
    current_refs: Mapping[str, object],
    not_observed: set[str] | frozenset[str] = frozenset(),
) -> DeltaPlan:
    """Choose complete or incremental delivery using only structured observation data."""
    matches_before = (
        True
        if (
            not isinstance(before, DisplayDocument)
            or not isinstance(after, DisplayDocument)
            or before.context != after.context
            or any(previous_refs[ref] != current_refs[ref] for ref in previous_refs.keys() & current_refs.keys())
        )
        else False
    )
    if matches_before:
        return DeltaPlan()
    old_trees = [part for part in before.parts if isinstance(part, DisplayTree)]
    new_trees = [part for part in after.parts if isinstance(part, DisplayTree)]
    if (
        not old_trees
        or len(old_trees) != len(new_trees)
        or any(not _compatible(old, new, previous_refs, current_refs) for old, new in zip(old_trees, new_trees))
    ):
        return DeltaPlan()
    deltas = [_compare(old, new) for old, new in zip(old_trees, new_trees)]
    _related_context(deltas)
    changed = any(delta.changes or delta.missing for delta in deltas)
    return DeltaPlan(
        "diff" if changed else "unchanged",
        tuple(
            TreeDelta(
                MappingProxyType(dict(delta.changes)),
                delta.missing,
                frozenset(delta.context),
                frozenset(delta.expanded),
            )
            for delta in deltas
        ),
        frozenset(not_observed),
        frozenset(current_refs),
    )


def snapshot_diff(previous: Record, current: Record, not_observed: set[str] | None = None) -> str:
    """Explicitly plan and render a structured observation diff at the text boundary."""
    from computer_use.interaction.read.presentation.rendering.document import render_document

    after = current.get("document")
    if not isinstance(after, DisplayDocument):
        return current["text"]
    plan = plan_document_diff(
        previous.get("document"), after, previous["refs"], current["refs"], set(not_observed or ())
    )
    return render_document(after, plan, public_api=current.get("publicApi", False))
