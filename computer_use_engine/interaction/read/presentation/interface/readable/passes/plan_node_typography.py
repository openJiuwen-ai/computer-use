# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select source-specific row, explanation and native-note placements."""

from __future__ import annotations

from types import MappingProxyType

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import ReadableDraft, ReadableNode, ReadablePlan, ReadableRequest
from ..rules import identifier_caption, passive_note_source, toggle_glyph


def execute(request: ReadableRequest, control: Control) -> ReadableDraft:
    """Keep controls in the body and place ancillary form facts in numbered notes."""
    form_refs: set[str] = set()
    labels: set[str] = set()
    visited: set[str] = set()
    for block in request.interface.blocks.values():
        if block.kind != "field" or block.visual_unit or block.label_ref is None:
            continue
        labels.add(block.label_ref)
        pending = [block.key]
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            member = request.interface.blocks[key]
            form_refs.update(member.source_refs)
            if member.primary_ref is not None and request.interface.projected_nodes[member.primary_ref]["role"] in (
                "textArea",
                "textField",
                "webArea",
                "table",
                "list",
                "outline",
                "document",
                "scrollArea",
            ):
                continue
            pending.extend(member.children)
    form_refs.update(labels)
    styles: dict[str, ReadableNode] = {}
    for ref, atom in request.atoms.atoms.items():
        role = request.interface.projected_nodes[ref]["role"]
        value = atom.slot("value")
        glyph = toggle_glyph(role, value.value) if value is not None else None
        caption_slots = tuple(
            slot.key
            for slot in atom.slots
            if ref in form_refs
            and slot.key in ("name", "value")
            and role in ("staticText", "image")
            and identifier_caption(slot.value)
        )
        empty_selection = tuple(slot.key for slot in atom.slots if slot.key == "selectedText" and slot.value == "")
        note_slots = (*caption_slots, *empty_selection)
        blocks = tuple(
            slot.key
            for slot in atom.slots
            if slot.key in ("help", "fullDescription")
            or slot.key == "description"
            and isinstance(slot.value, str)
            and len(slot.value) > 40
        )
        actions = tuple(
            action.catalogue_index
            for action in atom.actions
            if role not in ("window", "sheet", "dialog", "alert", "application")
            and action.source_ref == ref
            and action.source.get("kind") in ("raise", "increment", "decrement")
        )
        notes_only = bool(caption_slots) and passive_note_source(atom, note_slots)
        if note_slots or blocks or actions or glyph is not None:
            styles[ref] = ReadableNode(
                note_slots, blocks, actions, "value" if glyph is not None else None, glyph, notes_only
            )
    return ReadableDraft(request, ReadablePlan(MappingProxyType(styles)))


stage = Pass.define(
    name="PlanReadableNodeTypography",
    function=execute,
    contract=Contract(
        ReadableRequest,
        ReadableDraft,
        {Effect.PURE},
        requires=("source-bound atoms and validated field membership",),
        writes=("exact field placements and explicit toggle notation",),
    ),
)
