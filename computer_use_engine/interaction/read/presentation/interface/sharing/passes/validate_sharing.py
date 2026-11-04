# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Freeze source-specific sharing only after exact declaration/exclusion coverage checks."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..helpers import canonical, declaration_parent
from ..model import SharingDraft, SharingPlan


def execute(draft: SharingDraft, control: Control) -> SharingPlan:
    """Verify every omitted field/action belongs to exactly one valid local declaration."""
    request, plan = draft.request, draft.plan
    fields: dict[str, set[str]] = {}
    actions: dict[str, set[int]] = {}
    for key, declarations in plan.by_block.items():
        for group in declarations.actions:
            if len(group.refs) != len(set(group.refs)) or declaration_parent(request, group.refs) != key:
                raise ValueError("Shared actions have no unique source-backed display scope")
            expected = tuple((item.name, item.label, item.ambiguous, canonical(item.source)) for item in group.actions)
            for ref in group.refs:
                atom = request.atoms.atoms[ref]
                if ref in actions or any(item.source_ref != ref for item in atom.actions):
                    raise ValueError("Shared action occurrence has multiple or changed owners")
                actual = tuple((item.name, item.label, item.ambiguous, canonical(item.source)) for item in atom.actions)
                if actual != expected:
                    raise ValueError("Shared action declaration differs from a member occurrence")
                actions[ref] = {item.catalogue_index for item in atom.actions}
        for group in declarations.fields:
            if len(group.refs) != len(set(group.refs)) or declaration_parent(request, group.refs) != key:
                raise ValueError("Shared fields have no unique source-backed display scope")
            if tuple(ref for ref, _ in group.source_slots) != group.refs:
                raise ValueError("Shared field declaration lost its ordered source slots")
            for ref, slot in group.source_slots:
                if (
                    slot != request.atoms.atoms[ref].slot(group.slot_key)
                    or slot.key != group.slot.key
                    or slot.label != group.slot.label
                    or type(slot.value) is not type(group.slot.value)
                    or slot.value != group.slot.value
                ):
                    raise ValueError("Shared field declaration differs from an original slot")
                if group.slot_key in fields.setdefault(ref, set()):
                    raise ValueError("Shared field occurrence has multiple declarations")
                fields[ref].add(group.slot_key)
    if fields != {ref: set(items) for ref, items in plan.omitted_slots.items()}:
        raise ValueError("A suppressed field lacks an exact source declaration")
    if actions != {ref: set(items) for ref, items in plan.omitted_actions.items()}:
        raise ValueError("A suppressed action lacks an exact source declaration")
    return plan


stage = Pass.define(
    name="validate_interface_sharing",
    function=execute,
    contract=Contract(
        SharingDraft,
        SharingPlan,
        {Effect.PURE},
        requires=("local source declarations", "per-source exclusions"),
        writes=("validated immutable sharing plan",),
    ),
)
