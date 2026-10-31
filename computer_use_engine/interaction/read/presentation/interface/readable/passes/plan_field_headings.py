# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use an existing option question in place of opaque nested label captions."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import ReadableDraft, ReadableHeading


def execute(draft: ReadableDraft, control: Control) -> ReadableDraft:
    """Promote one exact local radio description and flatten redundant label tiers."""
    request = draft.request
    headings: dict[str, ReadableHeading] = {}
    for block in request.interface.blocks.values():
        ref = block.label_ref
        style = draft.plan.nodes.get(ref)
        if (
            block.kind != "field"
            or block.visual_unit
            or ref is None
            or style is None
            or not style.notes_only
            or block.heading_style != "caption"
        ):
            continue
        candidates = request.sharing.by_block.get(block.key)
        questions = tuple(
            (index, group)
            for index, group in enumerate(candidates.fields if candidates else ())
            if group.slot_key == "description"
            and isinstance(group.slot.value, str)
            and group.slot.value.strip()
            and len(group.refs) > 1
            and all(request.interface.projected_nodes[item]["role"] == "radioButton" for item in group.refs)
        )
        if len(questions) == 1:
            index, group = questions[0]
            headings[block.key] = ReadableHeading(ref, group.slot, group.refs, index)
        else:
            headings[block.key] = ReadableHeading(ref)
    # A redundant opaque tier can be omitted only when its sole content is the
    # already-established nested field. Every real source still has its own note.
    for key, heading in tuple(headings.items()):
        block = request.interface.blocks[key]
        content = tuple(child for child in block.children if request.interface.blocks[child].primary_ref != heading.ref)
        if heading.caption is None and len(content) == 1 and content[0] in headings:
            headings[key] = replace(heading, transparent=True)
    return replace(draft, plan=replace(draft.plan, headings=MappingProxyType(headings)))


stage = Pass.define(
    name="PlanReadableFieldHeadings",
    function=execute,
    contract=Contract(
        ReadableDraft,
        ReadableDraft,
        {Effect.PURE},
        requires=("numbered form-label notes and exact shared descriptions",),
        writes=("source-explicit question captions and transparent label tiers",),
    ),
)
