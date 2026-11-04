# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan exact repeated fields as local declarations with every source explicit."""

from __future__ import annotations

from collections import defaultdict

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.interface.atoms.model import AtomSlot
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..helpers import append_groups, canonical, declaration_parent, empty_value_groups, field_saving, member_scope
from ..model import SharedFieldGroup, SharingDraft, SharingRequest


def _empty_value_eligible(request: SharingRequest, ref: str) -> bool:
    """Keep input values and active or protected targets at their own displayed location."""
    atom = request.atoms.atoms[ref]
    node = atom.source_fields
    evidence = request.interface.source.context.get("compressionEvidence")
    if (
        not isinstance(evidence, CompressionEvidence)
        or ref in request.evidence.protected_values
        or ref in evidence.concrete_inputs
        or ref in evidence.semantic_regions
        or ref == request.atoms.focused_ref
        or ref in request.atoms.selected_refs
        or node.get("focused") is True
        or node.get("selected") is True
        or node.get("editable") is True
        or node.get("protected") is True
        or bool(node.get("selectedText"))
        or any(slot.protection in ("input", "focus") for slot in atom.slots)
    ):
        return False
    return True


def _auxiliary_text(request: SharingRequest, ref: str, key: str) -> bool:
    """Keep an object's only identifying caption or status beside that object.

    A description is supplemental only when a distinct public name already
    identifies this target. Values alone can be numeric measurements or states;
    their description supplies the meaning needed to interpret them.
    """
    atom = request.atoms.atoms[ref]
    captions = ("name", "description") if key == "help" else ("name",)
    return any(
        slot is not None
        and isinstance(slot.value, str)
        and bool(slot.value.strip())
        and slot.format == "text"
        and slot.preview is None
        for slot in (atom.slot(caption) for caption in captions)
    )


def execute(draft: SharingDraft, control: Control) -> SharingDraft:
    """Share only exact complete text slots, preserving each field's meaning and owner."""
    request = draft.request
    candidates: dict[tuple[object, ...], list[tuple[str, AtomSlot]]] = defaultdict(list)
    for ref in request.atoms.order:
        if ref in request.excluded_sources:
            continue
        atom = request.atoms.atoms[ref]
        if ref in request.evidence.contested_sources:
            continue
        for slot in atom.slots:
            supported = (
                slot.key in ("help", "description")
                and bool(slot.value)
                and _auxiliary_text(request, ref, slot.key)
                or slot.key == "value"
                and slot.value == ""
                and _empty_value_eligible(request, ref)
            )
            if (
                not supported
                or type(slot.value) is not str
                or slot.preview is not None
                or slot.protection is not None
                or slot.placement != "inline"
                or not slot.facts
                or request.disclosure is not None
                and request.disclosure.get(ref, slot.key) is not None
                or any(
                    (fact.key.source, fact.key.attribute) not in request.evidence.complete_fields
                    or fact.value != slot.value
                    or type(fact.value) is not type(slot.value)
                    for fact in slot.facts
                )
            ):
                continue
            scope = member_scope(request, ref)
            # Sharing a value does not share capability or action ownership.
            # Keep window/menu and blocking scope while each node retains its
            # independent role, operation capabilities and current states.
            if slot.key == "value":
                scope = (scope[0], scope[1], canonical(atom.source_fields.get("blockedBy", ())))
            key = (scope, slot.key, slot.label, slot.format, slot.placement, canonical(slot.value))
            candidates[key].append((ref, slot))
    selected: dict[str, list[SharedFieldGroup]] = defaultdict(list)
    for members in candidates.values():
        groups = empty_value_groups(request, members) if members[0][1].key == "value" else (members,)
        for group in groups:
            refs = tuple(ref for ref, _ in group)
            key = declaration_parent(request, refs)
            if key is None:
                continue
            slot = group[0][1]
            saved = field_saving(refs, slot)
            if saved > 0:
                selected[key].append(SharedFieldGroup(refs, slot.key, slot, tuple(group), saved))
    return append_groups(draft, fields=selected)


stage = Pass.define(
    name="share_interface_complete_fields",
    function=execute,
    contract=Contract(
        SharingDraft,
        SharingDraft,
        {Effect.PURE},
        requires=("complete exact text occurrences", "known common source parent"),
        writes=("local shared field declarations", "per-source slot exclusions"),
    ),
)
