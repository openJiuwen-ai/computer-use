# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select exact form captions and help slots while retaining independent operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.atoms.model import (
    AtomDisposition,
    AtomDocument,
    AtomFieldClaim,
    AtomPlan,
    AtomSlot,
)
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument

from .model import (
    FieldContentPlan,
    FieldContentRequest,
    FieldContribution,
    FieldMemberPresentation,
    FieldPresentation,
)
from .duplicate_caption import duplicate_caption_slots


def _members(document: InterfaceDocument, key: str) -> tuple[str, ...]:
    refs = []
    pending = list(reversed(document.blocks[key].children))
    while pending:
        block = document.blocks[pending.pop()]
        refs.extend(block.source_refs)
        pending.extend(reversed(block.children))
    return tuple(refs)


def _content_slots(atom: AtomPlan, atoms: AtomDocument) -> tuple[AtomSlot, ...]:
    """Accept affirmative passive text whose every displayed slot is the same caption."""
    source = atom.source_fields
    caps = source.get("capabilities")
    required = ("click", "typeText", "replaceText", "focus")
    if (
        source.get("role") != "staticText"
        or atom.children
        or atom.states
        or atom.coverage.partial
        or atom.actions_unavailable
        or atom.declarations
        or atoms.focused_ref == atom.ref
        or not isinstance(caps, Mapping)
        or any(caps.get(key) != "unavailable" for key in required)
        or "actions" not in source
        or any(
            source.get(key)
            for key in (
                "selected",
                "focused",
                "busy",
                "invalid",
                "modal",
                "incompleteLabelRelation",
                "actionsUnavailable",
                "preserveGroup",
            )
        )
        or source.get("enabled") is False
    ):
        return ()
    slots = atom.slots
    if not slots or any(
        slot.key not in ("name", "description", "value")
        or slot.protection
        or slot.format != "text"
        or not isinstance(slot.value, str)
        or not slot.value.strip()
        or any(char in slot.value for char in ("\n", "\r", "\t"))
        for slot in slots
    ):
        return ()
    complete = source.get("_completeTextValues", ())
    evidence = atoms.source.context.get("compressionEvidence")
    if any(
        slot.key not in complete
        and not (
            isinstance(evidence, CompressionEvidence)
            and slot.facts
            and all((fact.key.source, fact.key.attribute) in evidence.complete_text for fact in slot.facts)
        )
        for slot in slots
    ):
        return ()
    value = slots[0].value
    return slots if all(slot.value == value for slot in slots) else ()


def _caption_destination(slots: list[AtomSlot], source_ref: str, incoming: tuple[AtomSlot, ...]) -> str | None:
    captions = [slot for slot in slots if slot.placement == "caption"]
    if captions and (len(captions) != 1 or captions[0].value != incoming[0].value):
        return None
    facts = tuple(fact for slot in incoming for fact in slot.facts)
    if captions:
        destination = captions[0]
        slots[slots.index(destination)] = replace(destination, facts=(*destination.facts, *facts))
        return destination.key
    key = "field-label:" + source_ref
    slots.insert(0, replace(incoming[0], key=key, label=None, placement="caption", facts=facts))
    return key


def _help_destination(slots: list[AtomSlot], source_ref: str, incoming: tuple[AtomSlot, ...]) -> str:
    equivalent = next(
        (
            slot
            for slot in slots
            if (slot.key == "help" or slot.key.startswith("field-help:")) and slot.value == incoming[0].value
        ),
        None,
    )
    facts = tuple(fact for slot in incoming for fact in slot.facts)
    if equivalent is not None:
        slots[slots.index(equivalent)] = replace(equivalent, facts=(*equivalent.facts, *facts), placement="block")
        return equivalent.key
    key = "field-help:" + source_ref
    slots.append(replace(incoming[0], key=key, label="Help", placement="block", facts=facts))
    return key


def _dispositions(
    atom: AtomPlan, target: str, destination: str, slots: tuple[AtomSlot, ...]
) -> tuple[AtomDisposition, ...]:
    """Move selected field occurrences and explicitly retain source-only metadata in details."""
    consumed = {slot.key for slot in slots}
    return tuple(
        replace(disposition, target_ref=target, slot=destination, kind="equivalent")
        if disposition.slot in consumed
        else replace(disposition, kind="details")
        for disposition in atom.dispositions
    )


def _claims(atom: AtomPlan, target: str, slots: tuple[AtomSlot, ...]) -> tuple[AtomFieldClaim, ...]:
    represented = {(fact.key.source, fact.key.attribute) for slot in slots for fact in slot.facts}
    return tuple(
        replace(claim, target=target, kind="equivalent")
        for claim in atom.field_claims
        if (claim.ref, claim.field) in represented
    )


def plan_fields(request: FieldContentRequest) -> FieldContentPlan:
    """Compose eligible source text into already established form memberships.

    Args:
        request: Source-organized field blocks, typed atoms and a guarantee that
            omitted source metadata remains reachable in this captured observation.

    Returns:
        Selected slots and exact per-member dispositions. Every uncomposed member
        remains complete, and native actions retain their own source reference.
    """
    document, atoms = request.interface, request.atoms
    if document.source is not atoms.source:
        raise ValueError("Field composition requires the same captured source graph")
    result = {}
    for key, block in document.blocks.items():
        if block.layout == "compound-control":
            continue
        target = block.field_target_ref
        if target is None or target not in atoms.atoms:
            continue
        target_atom = atoms.atoms[target]
        slots = list(target_atom.slots)
        roles = dict(block.member_roles)
        members, contributions, dispositions, claims = [], [], [], []
        for ref in _members(document, key):
            if ref == target:
                continue
            atom = atoms.atoms[ref]
            incoming = _content_slots(atom, atoms) if request.captured_details else ()
            role = roles.get(ref)
            duplicate = (
                duplicate_caption_slots(atom, target_atom, block, document, atoms)
                if request.captured_details and role == "label"
                else ()
            )
            if duplicate:
                incoming = duplicate
            destination = None
            if incoming and role == "label":
                destination = _caption_destination(slots, ref, incoming)
            elif incoming and role == "help":
                destination = _help_destination(slots, ref, incoming)
            if destination is None:
                members.append(FieldMemberPresentation(ref, "complete"))
                continue
            contributions.extend(FieldContribution(ref, slot, destination) for slot in incoming)
            mode = "caption-reference" if duplicate else "action-source" if atom.actions else "composed"
            members.append(FieldMemberPresentation(ref, mode, tuple(slot.key for slot in incoming)))
            dispositions.extend(_dispositions(atom, target, destination, incoming))
            claims.extend(_claims(atom, target, incoming))
        heading = (
            block.label_ref
            if any(member.ref == block.label_ref and member.mode == "complete" for member in members)
            else None
        )
        result[key] = FieldPresentation(
            key, target, tuple(slots), tuple(members), tuple(contributions), tuple(dispositions), tuple(claims), heading
        )
    return FieldContentPlan(MappingProxyType(result))
