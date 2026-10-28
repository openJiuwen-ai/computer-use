# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure range selection and representation costs for contiguous passive groups."""

from __future__ import annotations

from math import ceil
from urllib.parse import quote

from computer_use.interaction.read.presentation.interface.atoms.model import AtomSlot
from computer_use.interaction.read.presentation.interface.disclosure.policy import estimate_units
from .evidence import OUTLINE_ROLES, RECORD_ROLES, recovery_sources
from .model import CapturedGroupField, ClassifiedGroups, GroupEntry, GroupField, GroupFragment, GroupOverview


def captured_text_fields(fields: tuple[GroupField, ...]) -> tuple[CapturedGroupField, ...]:
    """Return each original string-field occurrence once, preserving source order and type."""
    sources: dict[tuple[str, str], CapturedGroupField] = {}
    for field in fields:
        for source in field.sources:
            if type(source.value) is str:
                key = (source.ref, source.native_field)
                previous = sources.setdefault(key, source)
                if previous.value != source.value:
                    raise ValueError("One captured source field has conflicting payloads")
    return tuple(sources.values())


def fragments(fields: tuple[GroupField, ...], budget: int, *, tail: bool = False) -> tuple[GroupFragment, ...]:
    """Select exact leading or trailing characters without joining distinct source fields."""
    result: list[GroupFragment] = []
    sources = captured_text_fields(fields)
    for source in reversed(sources) if tail else sources:
        value = source.value
        if not value or budget <= 0:
            continue
        size = min(len(value), budget)
        start, end = (len(value) - size, len(value)) if tail else (0, size)
        result.append(GroupFragment(source.ref, source.attribute, start, end, value[start:end], source))
        budget -= size
    return tuple(reversed(result)) if tail else tuple(result)


def capture_entries(classified: ClassifiedGroups, refs: tuple[str, ...]) -> tuple[GroupEntry, ...]:
    """Retain each atomic field with every exact native fact that contributes to it."""
    atoms = classified.request.atoms.atoms

    def source_field(ref: str, slot: AtomSlot) -> GroupField:
        sources = tuple(
            source
            for fact in slot.facts
            for source in classified.native_fields.get((str(fact.key.source), fact.key.attribute), ())
            if type(source.normalized) is type(fact.value) and source.normalized == fact.value
        )
        return GroupField(ref, slot.key, slot, sources)

    return tuple(
        GroupEntry(
            ref, atoms[ref].parent, atoms[ref].role.label, tuple(source_field(ref, slot) for slot in atoms[ref].slots)
        )
        for ref in refs
    )


def candidate(
    classified: ClassifiedGroups, parent_key: str, children: tuple[str, ...], recovery_prefix_units: int
) -> tuple[GroupOverview | None, str]:
    """Build a candidate and charge both initial overview and reachable recovery costs."""
    request, policy = classified.request, classified.request.policy
    projected_refs = tuple(ref for key in children for ref in classified.block_refs[key])
    if len(projected_refs) < policy.minimum_objects:
        return None, "too-few-passive-objects"
    refs = recovery_sources(request, projected_refs)
    if any(ref in classified.protections for ref in refs):
        return None, "protected-source-or-folded-contributor"
    if len({request.interface.source.scopes.get(ref) for ref in refs}) != 1:
        return None, "distinct-native-scopes"
    entries = capture_entries(classified, projected_refs)
    fields = tuple(field for entry in entries for field in entry.fields)
    if any(
        not field.sources
        or any(
            not any(
                source.ref == str(fact.key.source)
                and type(source.normalized) is type(fact.value)
                and source.normalized == fact.value
                for source in field.sources
            )
            for fact in field.slot.facts
        )
        for field in fields
    ):
        return None, "unresolved-native-field"
    characters = sum(len(source.value) for source in captured_text_fields(fields))
    if characters < policy.minimum_characters:
        return None, "short-captured-content"
    outline = tuple(
        field
        for field in fields
        if field.slot_key == "name"
        and request.snapshot.native.nodes[request.snapshot.refs[field.ref]].role in OUTLINE_ROLES
    )
    body = tuple(field for field in fields if field not in outline)
    head = fragments(body, policy.head_characters)
    tail = fragments(body, policy.tail_characters, tail=True)
    if not head and not tail:
        return None, "no-original-body-excerpt"
    # Existing field excerpts are already cheap; they must not be counted as a
    # full long string again when estimating the additional grouping benefit.
    full_units = 0
    for entry in entries:
        full_units += estimate_units(entry.ref + entry.role) + 3
        for field in entry.fields:
            excerpt = request.field_disclosure.get(field.ref, field.slot_key) if request.field_disclosure else None
            if excerpt is not None:
                full_units += sum(estimate_units(part.text) for part in excerpt.fragments) + 40
            else:
                full_units += estimate_units(str(field.slot.value)) + estimate_units(field.slot.label or "") + 1
    # Directory, range descriptors and the real recovery instruction all cost
    # tokens. Sparse refs are charged independently, not treated as one range.
    native = request.snapshot.native
    capture = (native.backend, native.session, native.id)
    # Component-wise escaping is injective, keeping capture-local aliases
    # unambiguous across windows without hashes or invented operation targets.
    section_key = "content:" + ":".join(quote(value, safe="") for value in (*capture, projected_refs[0]))
    overview_units = (
        100
        + 2 * estimate_units(section_key)
        + sum(estimate_units(field.ref + str(field.slot.value)) + 5 for field in outline)
        + sum(estimate_units(part.text) + estimate_units(part.ref + part.slot_key) + 8 for part in (*head, *tail))
        + sum(estimate_units(ref) + 1 for ref in refs)
    )
    own_recovery_units = (
        80
        + 3 * estimate_units(section_key)
        + sum(estimate_units(entry.ref + entry.role) + 8 for entry in entries)
        + sum(estimate_units(str(field.slot.value)) + estimate_units(field.slot_key) + 5 for field in fields)
        + sum(
            estimate_units(str(fact.value)) + 8
            for field in fields
            for fact in field.slot.facts
            if type(fact.value) is not type(field.slot.value) or fact.value != field.slot.value
        )
        + sum(
            estimate_units(str(source.value)) + 8
            for field in fields
            for source in field.sources
            if type(source.value) is not type(source.normalized) or source.value != source.normalized
        )
    )
    recovery_units = recovery_prefix_units + own_recovery_units
    calls = ceil(4 * recovery_units / policy.recovery_page_characters)
    saved = (
        full_units - overview_units - ceil(recovery_units * policy.recovery_weight) - calls * policy.recovery_call_units
    )
    if saved < policy.minimum_saved_units or saved < full_units * policy.minimum_saved_fraction:
        return None, "insufficient-benefit-after-recovery-cost"
    records = all(
        (ref := request.interface.blocks[key].primary_ref) is not None
        and request.snapshot.native.nodes[request.snapshot.refs[ref]].role in RECORD_ROLES
        for key in children
    )
    group = GroupOverview(
        section_key,
        capture,
        parent_key,
        children,
        projected_refs,
        refs,
        entries,
        fields,
        outline,
        head,
        tail,
        "records" if records else "content",
        characters,
        len(children) if records else None,
        full_units,
        overview_units,
        recovery_units,
        saved,
    )
    return group, "beneficial-complete-passive-run"
