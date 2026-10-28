# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select native URL detail routes from complete planned captions and peer evidence."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.interaction.read.presentation.rendering.native_urls import (
    EmittedCaption,
    capture_native_url_sources,
    select_deferred_native_urls,
)
from .model import AtomDisposition, AtomDocument, AtomFinalizeRequest

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomSlot
    from computer_use.interaction.read.presentation.ir import ProjectionResult


def _complete_caption(projection: ProjectionResult, ref: str, slot: AtomSlot) -> EmittedCaption:
    """Prove a retained caption slot from exact source occurrences before serialization."""
    source = projection.provenance.get((ref, slot.key))
    evidence = projection.source.context.get("compressionEvidence")
    facts = tuple(key for part in source.parts for key in part.facts) if source is not None else ()
    complete = (
        type(slot.value) is str
        and bool(slot.value)
        and slot.preview is None
        and bool(facts)
        and isinstance(evidence, CompressionEvidence)
        and source.value == slot.value
        and all((fact.source, fact.attribute) in evidence.complete_text for fact in facts)
    )
    return EmittedCaption(slot.key, slot.value, facts, bool(complete))


def finalize_native_urls(request: AtomFinalizeRequest) -> AtomDocument:
    """Move proven redundant native URLs to captured details without changing target identity.

    Caption equality uses complete raw planned values. Any shortened or excerpted
    caption is excluded as proof. All visible peers, including incomplete URL reads,
    still participate in the existing destination-ambiguity check.
    """
    branch, projection, document = request.branch, request.projection, request.atoms
    if not branch.request.compact:
        return document
    sources = capture_native_url_sources(branch.request.snapshot, projection)
    nodes = {}
    parents = {}
    pending = [(node, None) for node in reversed(projection["roots"])]
    while pending:
        node, parent = pending.pop()
        nodes[node["ref"]] = node
        parents[node["ref"]] = parent
        pending.extend((child, node["ref"]) for child in reversed(node["children"]))
    captions: dict[str, tuple[EmittedCaption, ...]] = {}
    candidates = []
    for ref, atom in document.atoms.items():
        if atom.slot("url") is not None:
            candidates.append(ref)
        fragments = []
        for slot in atom.slots:
            if slot.key not in ("name", "description", "value") or type(slot.value) is not str or not slot.value:
                continue
            excerpt = request.disclosure.get(ref, slot.key) if request.disclosure is not None else None
            if excerpt is not None or slot.preview is not None:
                # A shortened caption still defines a peer's public context, but
                # cannot establish complete identification for URL hiding.
                fragments.append(EmittedCaption(slot.key, slot.value, (), False))
            else:
                fragments.append(_complete_caption(projection, ref, slot))
        if fragments:
            captions[ref] = tuple(fragments)
    plan = select_deferred_native_urls(sources, nodes, parents, captions, candidates)
    if not plan.destinations:
        return document
    atoms = dict(document.atoms)
    for destination in plan.destinations:
        ref = destination["ref"]
        atom = atoms[ref]
        slot = atom.slot("url")
        if slot is None:
            raise ValueError("Native URL destination has no planned source field")
        retained = tuple(item for item in atom.dispositions if item.slot != "url")
        deferred = tuple(AtomDisposition(fact, ref, "url", "details") for fact in slot.facts)
        atoms[ref] = replace(
            atom,
            slots=tuple(item for item in atom.slots if item.key != "url"),
            dispositions=(*retained, *deferred),
            field_claims=tuple(item for item in atom.field_claims if not (item.ref == ref and item.field == "url")),
        )
    return replace(
        document,
        atoms=MappingProxyType(atoms),
        dispositions=tuple(item for ref in document.order for item in atoms[ref].dispositions),
        field_claims=tuple(item for ref in document.order for item in atoms[ref].field_claims),
        deferred_native_urls=tuple(freeze(item) for item in plan.destinations),
        deferred_native_url_peers=tuple(freeze(item) for item in plan.peer_groups),
    )
