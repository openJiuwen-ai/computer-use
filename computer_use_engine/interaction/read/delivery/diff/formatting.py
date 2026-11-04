# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan incremental delivery from immutable display facts and containment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument, ComparisonNode, ComparisonTree
from computer_use.interaction.read.presentation.delta import DeltaPlan, TreeDelta
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import PreparedNode
    from computer_use.protocol.records import Record


@dataclass
class _TreeComparison:
    before: ComparisonTree
    after: ComparisonTree
    display: DisplayTree
    changes: dict[str, str] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    context: set[str] = field(default_factory=set)
    expanded: set[str] = field(default_factory=set)


def _related(node: PreparedNode) -> tuple[str, ...]:
    fields = node.fields
    return tuple(
        dict.fromkeys((*fields.get("labelTargets", ()), *fields.get("_contextRefs", ()), *fields.get("related", ())))
    )


def _compatible(
    before: ComparisonTree,
    after: ComparisonTree,
    previous_refs: Mapping[str, object],
    current_refs: Mapping[str, object],
) -> bool:
    """Check identity and containment; changing observations are the diff's input."""
    if before.scope != after.scope:
        return False
    shared = {node.ref for node in before.nodes} & {node.ref for node in after.nodes}
    if any(previous_refs.get(ref) != current_refs.get(ref) for ref in shared):
        return False
    for tree, bindings in ((before, previous_refs), (after, current_refs)):
        seen: dict[str, ComparisonNode] = {}
        for node in tree.nodes:
            if node.ref in seen or node.ref not in bindings:
                return False
            if node.parent is not None and (node.parent not in seen or seen[node.parent].depth + 1 != node.depth):
                return False
            seen[node.ref] = node
    return True


def _compare(before: ComparisonTree, after: ComparisonTree, display: DisplayTree) -> _TreeComparison:
    old, new = {node.ref: node for node in before.nodes}, {node.ref: node for node in after.nodes}
    delta = _TreeComparison(
        before,
        after,
        display,
        {node.ref: "~" if node.ref in old else "+" for node in after.nodes if old.get(node.ref) != node},
        tuple(node.ref for node in before.nodes if node.ref not in new),
    )
    # Representation changes refresh their own members, never invalidate the
    # identity of the window. Related roots are ordinary evolving AX relations.
    old_contexts, new_contexts = dict(before.root_contexts), dict(after.root_contexts)
    old_versions, new_versions = dict(before.versions), dict(after.versions)
    options_changed = (before.compact, before.public_api, before.details) != (
        after.compact,
        after.public_api,
        after.details,
    )
    for root in display.prepared.roots:
        if options_changed or old_versions.get(root.root_ref) != new_versions.get(root.root_ref):
            for ref in root.members:
                if ref in new:
                    delta.changes[ref] = "~" if ref in old else "+"
        if old_contexts.get(root.root_ref) != new_contexts.get(root.root_ref):
            # A compressed root can hide its original ref. Include its current
            # displayed roots and their owners so changed relations remain usable.
            for ref in root.members:
                if ref in new and new[ref].parent is None:
                    delta.changes[ref] = "~" if ref in old else "+"
            for owner, _ in (*old_contexts.get(root.root_ref, ()), *new_contexts.get(root.root_ref, ())):
                if owner in new:
                    delta.context.add(owner)
    # Reordering and reparenting are ordinary interface changes. Retain the
    # current paths and emit moved survivors even when their own fields match.
    old_order = [node.ref for node in before.nodes if node.ref in new]
    new_order = [node.ref for node in after.nodes if node.ref in old]
    for before_ref, after_ref in zip(old_order, new_order):
        if before_ref != after_ref:
            delta.changes[before_ref] = delta.changes[after_ref] = "~"
    children: dict[str, list[str]] = {}
    for node in display.nodes:
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
        if ref in old and new[ref].parent is not None and old[ref].content != new[ref].content:
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
    indexes = [{node.ref: node for node in delta.display.nodes} for delta in deltas]
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
                    parent.get("role") in ("group", "cell")
                    and not parent.get("name")
                    and not parent.get("description")
                    and len(members) > 1
                    and any(node.fields.get("role") in ("staticText", "heading") for node in members)
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
    before: ComparisonDocument | DisplayDocument | None,
    after: DisplayDocument | None,
    previous_refs: Mapping[str, object],
    current_refs: Mapping[str, object],
    not_observed: set[str] | frozenset[str] = frozenset(),
) -> DeltaPlan:
    """Choose complete or incremental delivery using only structured observation data."""
    if isinstance(before, DisplayDocument):
        before = ComparisonDocument.capture(before)
    matches_before = not isinstance(before, ComparisonDocument) or not isinstance(after, DisplayDocument)
    if matches_before:
        return DeltaPlan(reason="no-structured-baseline")
    # Directory focus and modal relationships may evolve without replacing a
    # tree. Native tree scopes and retained bindings below establish identity;
    # current document headers are always rendered, including changed context.
    old_trees = before.trees
    new_trees = [part for part in after.parts if isinstance(part, DisplayTree)]
    if not old_trees:
        return DeltaPlan()
    comparisons = []
    for display in new_trees:
        new = ComparisonTree.capture(display)
        candidates = [old for old in old_trees if old.scope == new.scope]
        old = candidates[0] if len(candidates) == 1 else None
        comparisons.append(
            _compare(old, new, display)
            if old is not None and _compatible(old, new, previous_refs, current_refs)
            else None
        )
    deltas = [delta for delta in comparisons if delta is not None]
    if not deltas:
        return DeltaPlan(reason="new-or-replaced-native-scope")
    _related_context(deltas)
    changed = any(delta is None or delta.changes or delta.missing for delta in comparisons)
    return DeltaPlan(
        "diff" if changed else "unchanged",
        tuple(
            TreeDelta(
                MappingProxyType(dict(delta.changes)),
                delta.missing,
                frozenset(delta.context),
                frozenset(delta.expanded),
            )
            if delta is not None
            else None
            for delta in comparisons
        ),
        frozenset(not_observed),
        frozenset(current_refs),
        "compared-native-scopes",
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
