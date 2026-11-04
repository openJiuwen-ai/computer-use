# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Defer independent native URLs only after complete captions were actually emitted."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.accounting.fields import SourceEmission
    from computer_use.interaction.read.presentation.ir import FactKey, ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, SnapshotFacts
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class NativeUrlSource:
    """One original URL destination, including unavailable peer destinations.

    Attributes:
        source: Original native object token, independent of the displayed parent.
        scope: Captured session, observation and original root reference.
        value: Exact original URL; None also represents an unknown peer destination.
        complete: Whether this uniquely bound URL field was read without a limitation.
        status: Original field-read status, when the provider reported one.
    """

    source: str
    scope: tuple[str, str, str]
    value: str | None
    complete: bool
    status: Mapping[str, object] | None = None


@dataclass(frozen=True)
class EmittedCaption:
    """An actual public caption fragment and the complete source facts it represents."""

    field: str
    text: str
    facts: tuple[FactKey, ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class NativeUrlPlan:
    """Selected field destinations with one shared ambiguity proof per peer group."""

    destinations: tuple[Record, ...] = ()
    peer_groups: tuple[Record, ...] = ()


def capture_native_url_sources(
    snapshot: SnapshotFacts,
    projection: ProjectionResult,
) -> Mapping[str, NativeUrlSource]:
    """Capture URL evidence for every source, including unknown and partial peers.

    Keeping unavailable peers in the index prevents their same-caption siblings from
    acquiring a false proof that all destinations are identical.
    """
    counts = Counter(snapshot.refs.values())
    facts = {node["node"]: node for node in snapshot.facts["nodes"]}
    global_gap = any(
        not issue.path or issue.path == ("nodes",) and issue.state != "partial" for issue in snapshot.native.issues
    )
    blocked = {
        issue.path[1]
        for issue in snapshot.native.issues
        if len(issue.path) >= 2 and issue.path[0] == "nodes" and (len(issue.path) == 2 or issue.path[2] == "url")
    }
    result = {}
    for ref, scope in projection.source.scopes.items():
        token = snapshot.refs[ref]
        original = snapshot.native.nodes.get(token)
        source = snapshot.fields.get((ref, "url"))
        native = facts.get(token, {})
        value = original.url if original is not None and type(original.url) is str else None
        status = native.get("fieldStatus", {}).get("url")
        failed = global_gap or token in blocked
        complete = (
            not failed
            and counts[token] == 1
            and value is not None
            and status is None
            and source is not None
            and source.get("field") == "url"
            and source.get("node") == token
            and tuple(source.get("path", ())) == ("url",)
            and type(source.get("normalized")) is str
            and source["normalized"] == value
        )
        result[ref] = NativeUrlSource(
            token,
            (snapshot.native.session, snapshot.native.id, scope),
            value,
            complete,
            None if status is None else MappingProxyType(dict(status)),
        )
    return MappingProxyType(result)


def emitted_caption(
    projection: ProjectionResult,
    node: NodeView,
    attribute: str,
    text: str,
    emissions: Sequence[SourceEmission],
) -> EmittedCaption:
    """Prove complete caption delivery from its actual scalar emission and provenance."""
    value = node.get(attribute)
    presented = projection.provenance.get((node["ref"], attribute))
    evidence = projection.source.context.get("compressionEvidence")
    facts = tuple(fact for part in presented.parts for fact in part.facts) if presented else ()
    complete = type(value) is str and bool(value) and bool(facts) and isinstance(evidence, CompressionEvidence)
    if complete:
        complete = all(
            (fact.source, fact.attribute) in evidence.complete_text
            and any(
                emission.fact == fact
                and emission.target == node["ref"]
                and emission.field == attribute
                and emission.kind != "continuation"
                and type(emission.value) is type(part.value)
                and emission.value == part.value
                for emission in emissions
            )
            for part in presented.parts
            for fact in part.facts
        )
    return EmittedCaption(attribute, text, facts, bool(complete))


def select_deferred_native_urls(
    sources: Mapping[str, NativeUrlSource],
    nodes: Mapping[str, NodeView],
    parents: Mapping[str, str | None],
    captions: Mapping[str, tuple[EmittedCaption, ...]],
    candidates: Sequence[str],
) -> NativeUrlPlan:
    """Select independently labelled destinations without hiding local ambiguity.

    Compare all final peer objects. Anonymous technical groups broaden the comparison
    scope, while named, positioned and non-group containers retain their visible context.
    This comparison changes no presentation parent or native target.
    """
    contexts: dict[str, str | None] = {}

    def context(ref: str) -> str | None:
        if ref in contexts:
            return contexts[ref]
        trail = []
        parent = parents[ref]
        while parent is not None:
            node = nodes[parent]
            if node["role"] != "group" or captions.get(parent) or node.get("spatialPosition"):
                break
            if parent in contexts:
                parent = contexts[parent]
                break
            trail.append(parent)
            parent = parents[parent]
        contexts[ref] = parent
        for anonymous in trail:
            contexts[anonymous] = parent
        return parent

    groups: dict[tuple[tuple[str, str, str], str | None, tuple[str, ...]], list[str]] = {}
    for ref in nodes:
        source = sources.get(ref)
        fragments = captions.get(ref, ())
        if source is not None and fragments:
            groups.setdefault((source.scope, context(ref), tuple(item.text for item in fragments)), []).append(ref)
    # Validate and store each complete peer group once. Repeated same-caption controls
    # refer to this proof instead of copying all k peers into each of k destinations.
    proofs: dict[tuple[tuple[str, str, str], str | None, tuple[str, ...]], Record] = {}
    for key, peers in groups.items():
        first = sources[peers[0]]
        if first.complete and all(sources[peer].complete and sources[peer].value == first.value for peer in peers):
            proofs[key] = dict(
                scope=key[0],
                context=key[1],
                caption=key[2],
                peers=tuple(
                    dict(ref=peer, native=sources[peer].source, url=sources[peer].value, readState="complete")
                    for peer in peers
                ),
            )
    result, published_groups = [], []
    group_ids: dict[tuple[tuple[str, str, str], str | None, tuple[str, ...]], int] = {}
    for ref in candidates:
        node, source = nodes[ref], sources.get(ref)
        fragments = captions.get(ref, ())
        if (
            node["role"] not in ("link", "image")
            or source is None
            or not source.complete
            or not source.value
            or node.get("url") != source.value
            or not any(item.complete for item in fragments)
        ):
            continue
        key = source.scope, context(ref), tuple(item.text for item in fragments)
        if key not in proofs:
            continue
        if key not in group_ids:
            group_ids[key] = len(published_groups)
            published_groups.append(dict(index=group_ids[key], **proofs[key]))
        result.append(
            dict(
                ref=ref,
                native=source.source,
                scope=source.scope,
                path=("url",),
                url=source.value,
                readState="complete",
                status=None,
                caption=[
                    dict(
                        field=item.field,
                        text=item.text,
                        complete=item.complete,
                        facts=[dict(ref=fact.source, field=fact.attribute) for fact in item.facts],
                    )
                    for item in fragments
                ],
                peerGroup=group_ids[key],
            )
        )
    return NativeUrlPlan(tuple(result), tuple(published_groups))
