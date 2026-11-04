# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate exact catalogue membership, protection and excerpt ranges before publication."""

from __future__ import annotations

from types import MappingProxyType

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..evidence import recovery_sources
from ..model import BackgroundOverview, GroupDisclosurePlan, SelectedGroups
from ..planning import captured_text_fields
from ..background import retained_notices


def execute(selected: SelectedGroups, control: Control) -> GroupDisclosurePlan:
    """Freeze reversible groups only after every original source and field is accounted for."""
    classified, omitted, seen = selected.classified, {}, set()
    request = classified.request
    native = request.snapshot.native
    for group in selected.groups:
        children = request.interface.blocks[group.parent_key].children
        start = children.index(group.child_keys[0])
        if children[start : start + len(group.child_keys)] != group.child_keys:
            raise ValueError("Disclosure group must replace consecutive existing child blocks")
        refs = tuple(ref for key in group.child_keys for ref in classified.block_refs[key])
        if refs != group.projected_refs or seen.intersection(refs):
            raise ValueError("Disclosure group source membership changed or overlapped")
        if group.recovery_refs != recovery_sources(request, refs):
            raise ValueError("Disclosure catalogue omitted a folded or composed source")
        background = isinstance(group, BackgroundOverview)
        if background:
            context = request.window_context
            root = request.interface.blocks[group.parent_key]
            if (
                context is None
                or not context.scope_confirmed
                or context.current
                or context.modal is True
                or not context.confirmed_blockers
                or group.blockers != context.confirmed_blockers
                or group.window_ref != context.window_ref
                or group.directory_complete != context.directory_complete
                or root.primary_ref is None
                or request.snapshot.refs[root.primary_ref] not in native.roots
                or group.child_keys != root.children
                or group.kind != "background"
                or (group.important_refs, group.uncertain_refs, group.nested_windows, group.state_facts)
                != retained_notices(request, refs)
            ):
                raise ValueError("Background overview lacks its confirmed native window and modal boundary")
        elif any(ref in classified.protections for ref in group.recovery_refs):
            raise ValueError("Disclosure catalogue contains protected native meaning")
        if group.capture != (native.backend, native.session, native.id):
            raise ValueError("Disclosure catalogue belongs to a different capture")
        if (
            tuple(entry.ref for entry in group.entries) != refs
            or tuple(field for entry in group.entries for field in entry.fields) != group.fields
        ):
            raise ValueError("Disclosure catalogue must contain every original atomic field")
        for ref in group.recovery_refs:
            token = request.snapshot.refs.get(ref)
            if token not in native.nodes or not ref.isdecimal() or str(int(ref)) != ref:
                raise ValueError("Disclosure requires an unchanged published native reference")
        for entry in group.entries:
            atom = request.atoms.atoms[entry.ref]
            if (
                entry.parent != atom.parent
                or entry.role != atom.role.label
                or tuple(field.slot for field in entry.fields) != atom.slots
            ):
                raise ValueError("Disclosure catalogue changed atomic values or hierarchy")
            omitted[entry.ref] = frozenset(field.slot_key for field in entry.fields)
        fields = {(field.ref, field.slot_key): field for field in group.fields}
        if len(fields) != len(group.fields):
            raise ValueError("Disclosure catalogue has ambiguous field identities")
        for field in group.fields:
            for source in field.sources:
                permitted = tuple(
                    original
                    for fact in field.slot.facts
                    for original in classified.native_fields.get((str(fact.key.source), fact.key.attribute), ())
                )
                if source not in permitted:
                    raise ValueError("Disclosure native field differs from its captured occurrence")
        for part in (*group.head, *group.tail):
            source = part.source
            if source is None or source not in captured_text_fields(group.fields):
                raise ValueError("Disclosure excerpt must identify an exact captured native string")
            value = source.value
            if (
                part.ref != source.ref
                or part.slot_key != source.attribute
                or type(value) is not str
                or not 0 <= part.start < part.end <= len(value)
                or value[part.start : part.end] != part.text
            ):
                raise ValueError("Disclosure excerpt is not an exact original field range")
        if group.captured_characters != sum(len(source.value) for source in captured_text_fields(group.fields)):
            raise ValueError("Disclosure count must describe captured fields only")
        if group.estimated_saved_units <= 0:
            raise ValueError("Disclosure has no measured structural benefit")
        seen.update(refs)
    return GroupDisclosurePlan(selected.groups, MappingProxyType(omitted), classified.protections, selected.assessments)


stage = Pass.define(
    name="validate_group_disclosure_catalogue",
    function=execute,
    contract=Contract(
        SelectedGroups,
        GroupDisclosurePlan,
        {Effect.PURE},
        requires=("selected runs", "complete captured catalogue", "exact source ranges"),
        writes=("validated global-detail recovery scopes", "field destinations"),
    ),
)
