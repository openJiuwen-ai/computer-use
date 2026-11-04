# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Complete captured sources and request-local layout permission for geometry planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, TYPE_CHECKING

from computer_use.interaction.read.presentation.ir import freeze

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceRequest
    from computer_use.interaction.read.presentation.ir import PresentedValue, ProjectionInput, ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, SnapshotFacts
    from computer_use.protocol.observation import NativeNode, NativeObservation


@dataclass(frozen=True)
class OriginalParent:
    """An original parent report, independent of compressed display containment.

    Attributes:
        parent: Existing reference when one parent is supported without conflict.
        status: Confirmed edge/root, unresolved evidence, or semantic-only fallback.
        relations: Exact indexed native relationships supporting the result.
    """

    parent: str | None
    status: Literal["confirmed", "root", "unknown", "conflict", "semantic"]
    relations: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class OriginalAncestry:
    """Nearest-first original ancestors and the evidence that ended traversal."""

    refs: tuple[str, ...]
    complete: bool
    stop_reason: Literal["root", "unknown", "conflict", "cycle", "missing", "semantic"]


def _semantic_index(roots: tuple[NodeView, ...] | list[NodeView]) -> dict[str, NodeView]:
    """Index acquired semantic views without inventing an ownership edge for them."""
    nodes: dict[str, NodeView] = {}
    pending = list(reversed(freeze(roots)))
    while pending:
        node = pending.pop()
        ref = node["ref"]
        if ref in nodes:
            continue
        nodes[ref] = node
        pending.extend(reversed(node.get("children", ())))
    return nodes


def _projected_refs(projection: ProjectionResult) -> tuple[str, ...]:
    """Retain only actual projected occurrences, refusing duplicate source identities."""
    refs: list[str] = []
    seen: set[str] = set()
    pending = list(reversed(projection["roots"]))
    while pending:
        node = pending.pop()
        ref = node["ref"]
        if ref in seen or ref not in projection.source.display_nodes:
            raise ValueError("Geometric projection contains a duplicate or out-of-scope source")
        seen.add(ref)
        refs.append(ref)
        pending.extend(reversed(node["children"]))
    return tuple(refs)


def _native_parents(refs: Mapping[str, str], relations: tuple[Mapping[str, Any], ...]) -> Mapping[str, OriginalParent]:
    """Resolve captured parent evidence without substituting a presentation ancestor.

    Confirmed enumerated child membership can establish an edge even when other
    siblings were not acquired. An explicit uncertain parent report or competing
    native parents prevents that inference. All reports remain available verbatim.
    """
    aliases = {token: ref for ref, token in refs.items()}
    direct: dict[str, list[Mapping[str, Any]]] = {}
    incoming: dict[str, list[Mapping[str, Any]]] = {}
    for relation in relations:
        if relation["kind"] == "parent":
            direct.setdefault(relation["source"], []).append(relation)
        elif relation["kind"] == "children":
            for target in relation["targets"]:
                incoming.setdefault(target, []).append(relation)
    result = {}
    for ref, token in refs.items():
        reports = direct.get(token, ())
        children = incoming.get(token, ())
        evidence = tuple((*reports, *children))
        parent_tokens: set[str] = set()
        roots = False
        uncertain = False
        for relation in reports:
            targets = relation["targets"]
            complete = (
                relation["certainty"] == "confirmed"
                and relation["state"] in ("complete", "absent")
                and relation.get("enumeration", "enumerated") == "enumerated"
                and relation.get("offset", 0) == 0
                and relation.get("total", len(targets)) == len(targets)
                and tuple(relation.get("positions", range(len(targets)))) == tuple(range(len(targets)))
            )
            if not complete:
                uncertain = True
            elif not targets:
                roots = True
            else:
                parent_tokens.update(targets)
        for relation in children:
            if (
                relation["certainty"] == "confirmed"
                and relation["state"] in ("complete", "partial")
                and relation.get("enumeration", "enumerated") == "enumerated"
            ):
                parent_tokens.add(relation["source"])
            else:
                uncertain = True
        if len(parent_tokens) > 1 or roots and parent_tokens:
            result[ref] = OriginalParent(None, "conflict", evidence)
        elif uncertain:
            result[ref] = OriginalParent(None, "unknown", evidence)
        elif parent_tokens:
            parent = aliases.get(next(iter(parent_tokens)))
            result[ref] = OriginalParent(parent, "confirmed" if parent is not None else "unknown", evidence)
        elif roots:
            result[ref] = OriginalParent(None, "root", evidence)
        else:
            # NativeObservation.roots are requested entry points, not proof that
            # these objects have no native parent.
            result[ref] = OriginalParent(None, "unknown", evidence)
    return MappingProxyType(result)


@dataclass(frozen=True)
class PreparedGeometricSources:
    """Immutable facts available to final layout, with a separate permitted scope.

    Attributes:
        source: Original pre-compression semantic source and GeometryIndex.
        semantic_nodes: Every captured semantic node by existing display reference.
            These normalized dictionaries are distinct from NativeNode objects.
        native: Complete immutable NativeObservation, when supplied by acquisition.
        native_nodes: Original NativeNode records keyed by native token.
        ref_to_native: Captured aliases; this contract allocates no new references.
        facts: Complete indexed capture, including gaps, relationships and actions.
        fields: Exact normalized-field paths and source accounting from acquisition.
        source_order: Pre-compression traversal of this request's permitted scope.
        captured_order: Capture enumeration order; it does not imply reading order.
        allowed_layout_refs: Original request members, including subsequently folded
            sources. Other captured windows remain queryable but outside this set.
        projection_refs: Current surviving projection occurrences in display order.
        parent_reports: Native parent evidence, or explicitly semantic-only fallback.
        folded_into: Existing source-fold decisions retained as reversible evidence.
        provenance: Per-field composition and individual original occurrences.
    """

    source: ProjectionInput
    semantic_nodes: Mapping[str, NodeView]
    native: NativeObservation | None
    native_nodes: Mapping[str, NativeNode]
    ref_to_native: Mapping[str, str]
    facts: Mapping[str, Any]
    fields: Mapping[tuple[str, str], Mapping[str, Any]]
    source_order: tuple[str, ...]
    captured_order: tuple[str, ...]
    allowed_layout_refs: frozenset[str]
    projection_refs: tuple[str, ...]
    parent_reports: Mapping[str, OriginalParent]
    folded_into: Mapping[str, str]
    provenance: Mapping[tuple[str, str], PresentedValue]

    @classmethod
    def build(cls, request: InterfaceRequest, snapshot: SnapshotFacts | None = None) -> PreparedGeometricSources:
        """Capture a request with optional same-observation acquisition evidence."""
        if snapshot is None:
            snapshot = getattr(request, "snapshot", None)
        return cls.capture(request.projection, snapshot)

    @classmethod
    def capture(cls, projection: ProjectionResult, snapshot: SnapshotFacts | None = None) -> PreparedGeometricSources:
        """Retain the complete sources without building a second display tree.

        Args:
            projection: Current view and its untouched pre-compression source.
            snapshot: The exact acquired observation behind that view. Standalone
                pure callers may omit it and retain semantic-source queries.

        Returns:
            Frozen source queries, original ancestry and explicit layout membership.

        Raises:
            ValueError: References are duplicated, outside scope, or incompatible
                with the captured observation.
        """
        source = projection.source
        allowed = frozenset(source.display_nodes)
        order = tuple(source.order)
        if len(order) != len(set(order)) or set(order) != allowed or not allowed <= source.native_nodes.keys():
            raise ValueError("Geometric source order must enumerate each permitted source once")
        projected = _projected_refs(projection)
        semantic = _semantic_index(snapshot.roots) if snapshot is not None else {}
        semantic.update(source.native_nodes)
        refs = dict(snapshot.refs) if snapshot is not None else {}
        if snapshot is not None and (
            len(set(refs.values())) != len(refs)
            or not semantic.keys() <= refs.keys()
            or not set(refs.values()) <= snapshot.native.nodes.keys()
        ):
            raise ValueError("Geometric captured aliases do not identify the same source observation")
        facts = freeze(snapshot.facts) if snapshot is not None else MappingProxyType({})
        fields = freeze(snapshot.fields) if snapshot is not None else MappingProxyType({})
        aliases = {token: ref for ref, token in refs.items()}
        captured = tuple(aliases[node["node"]] for node in facts.get("nodes", ()) if node["node"] in aliases)
        if snapshot is not None:
            parents = _native_parents(refs, tuple(facts.get("relations", ())))
        else:
            parents = MappingProxyType({ref: OriginalParent(source.parents.get(ref), "semantic") for ref in semantic})
            captured = tuple(dict.fromkeys((*order, *semantic)))
        folded = dict(projection.get("foldedInto", {}))
        if any(ref not in allowed or owner not in allowed for ref, owner in folded.items()):
            raise ValueError("Geometric source fold leaves the permitted request scope")
        for ref in folded:
            chain = {ref}
            current = ref
            while current in folded:
                current = folded[current]
                if current in chain:
                    raise ValueError("Geometric source folds contain a cycle")
                chain.add(current)
        provenance = {
            key: replace(value, parts=tuple(replace(part, value=freeze(part.value)) for part in value.parts))
            for key, value in projection.provenance.items()
        }
        return cls(
            source,
            MappingProxyType(semantic),
            snapshot.native if snapshot is not None else None,
            snapshot.native.nodes if snapshot is not None else MappingProxyType({}),
            MappingProxyType(refs),
            facts,
            fields,
            order,
            captured,
            allowed,
            projected,
            parents,
            MappingProxyType(folded),
            MappingProxyType(provenance),
        )

    def native_node(self, ref: str) -> NativeNode | None:
        """Find the exact captured native object for an existing public reference."""
        token = self.ref_to_native.get(ref)
        return self.native_nodes.get(token) if token is not None else None

    def original_parent(self, ref: str) -> str | None:
        """Return a supported original parent; inspect parent_reports for uncertainty."""
        report = self.parent_reports.get(ref)
        return report.parent if report is not None else None

    def ancestry(self, ref: str) -> OriginalAncestry:
        """Follow original edges once, preserving missing, conflicting and cyclic ends."""
        result = []
        seen = {ref}
        current = ref
        semantic = False
        while True:
            report = self.parent_reports.get(current)
            if report is None:
                return OriginalAncestry(tuple(result), False, "missing")
            if report.status in ("unknown", "conflict"):
                return OriginalAncestry(tuple(result), False, report.status)
            semantic |= report.status == "semantic"
            if report.parent is None:
                return OriginalAncestry(tuple(result), not semantic, "semantic" if semantic else "root")
            if report.parent in seen:
                return OriginalAncestry(tuple(result), False, "cycle")
            seen.add(report.parent)
            result.append(report.parent)
            current = report.parent

    def original_ancestors(self, ref: str) -> tuple[str, ...]:
        """Return nearest-first original ancestors without replacing unknown edges."""
        return self.ancestry(ref).refs

    def sources_for(self, ref: str) -> tuple[str, ...]:
        """List an object's own, folded and composed field sources in source order.

        Individual field occurrences remain in provenance; this query deduplicates
        identities only. Queryable out-of-scope sources never acquire layout permission.
        """
        if ref not in self.semantic_nodes:
            return ()
        members = {ref}
        for source in self.folded_into:
            owner = source
            while owner in self.folded_into:
                owner = self.folded_into[owner]
                if owner == ref:
                    members.add(source)
                    break
        for (owner, _), value in self.provenance.items():
            if owner == ref:
                members.update(fact.source for part in value.parts for fact in part.facts)
        order = tuple(dict.fromkeys((*self.source_order, *self.captured_order, *self.semantic_nodes)))
        return tuple(source for source in order if source in members)
