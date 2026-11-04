# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check every typography shortcut against its unchanged source inventory."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import ReadableDraft, ReadablePlan
from ..rules import passive_note_source, toggle_glyph
from ..associations import explanation_rows


def execute(draft: ReadableDraft, control: Control) -> ReadablePlan:
    """Require exact slots, action ownership and original radio-question evidence."""
    rows = explanation_rows(draft.request.interface, draft.request.atoms)
    for ref, style in draft.plan.nodes.items():
        atom = draft.request.atoms.atoms[ref]
        if not set((*style.note_slots, *style.block_slots)) <= {slot.key for slot in atom.slots}:
            raise ValueError("Readable typography refers to a missing source slot")
        if not set(style.note_actions) <= {
            action.catalogue_index for action in atom.actions if action.source_ref == ref
        }:
            raise ValueError("Readable native notes changed action ownership")
        if style.notes_only and not passive_note_source(atom, style.note_slots):
            raise ValueError("Readable note-only source retains independent visible facts or capabilities")
        if (style.toggle_slot is None) != (style.glyph is None):
            raise ValueError("Readable status requires both its explicit value slot and glyph")
        if style.visual_row and (rows.get(ref) != style.visual_row or "description" not in style.block_slots):
            raise ValueError("Readable image explanation lost its original visual row context")
        if style.toggle_slot is not None:
            slot = atom.slot(style.toggle_slot)
            role = draft.request.interface.projected_nodes[ref]["role"]
            if slot is None or style.glyph != toggle_glyph(role, slot.value):
                raise ValueError("Readable status differs from its explicit source value")
    for key, heading in draft.plan.headings.items():
        if draft.request.interface.blocks[key].label_ref != heading.ref:
            raise ValueError("Readable heading lost its original field label")
        style = draft.plan.nodes.get(heading.ref)
        if style is None or not style.notes_only:
            raise ValueError("Readable heading replacement lost its complete source note")
        if (heading.caption is None) != (heading.shared_index is None):
            raise ValueError("Readable question requires its original sharing declaration")
        if heading.shared_index is not None:
            group = draft.request.sharing.by_block[key].fields[heading.shared_index]
            if group.slot != heading.caption or group.refs != heading.sources:
                raise ValueError("Readable question changed its shared source fields")
            if group.slot_key != "description" or any(
                draft.request.interface.projected_nodes[ref]["role"] != "radioButton" for ref in heading.sources
            ):
                raise ValueError("Readable question lacks original radio-description evidence")
    return draft.plan


stage = Pass.define(
    name="ValidateReadableTypography",
    function=execute,
    contract=Contract(
        ReadableDraft,
        ReadablePlan,
        {Effect.PURE},
        requires=("original atoms and selected typography destinations",),
        writes=("validated immutable typography plan",),
    ),
)
