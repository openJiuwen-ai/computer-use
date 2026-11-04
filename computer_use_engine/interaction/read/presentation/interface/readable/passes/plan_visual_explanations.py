# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep a descriptive image's exact source and visible row context together."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..associations import explanation_rows
from ..model import ReadableDraft, ReadableNode


def execute(draft: ReadableDraft, control: Control) -> ReadableDraft:
    """Place an unshared image description below its already-established visual row."""
    request = draft.request
    styles = dict(draft.plan.nodes)
    for ref, members in explanation_rows(request.interface, request.atoms).items():
        if "description" in request.sharing.omitted_slots.get(ref, ()):
            continue
        style = styles.get(ref, ReadableNode())
        slot = request.atoms.atoms[ref].slot("description")
        # Short descriptions already sit beside their image reference. Preserve
        # those inline captions (including selected-record markers); only attach
        # context to explanations that prior content planning placed below a row.
        if slot is None or slot.placement != "block" and "description" not in style.block_slots:
            continue
        slots = tuple(dict.fromkeys((*style.block_slots, "description")))
        styles[ref] = replace(style, block_slots=slots, visual_row=members)
    return replace(draft, plan=replace(draft.plan, nodes=MappingProxyType(styles)))


stage = Pass.define(
    name="PlanVisualExplanations",
    function=execute,
    contract=Contract(
        ReadableDraft,
        ReadableDraft,
        {Effect.PURE},
        requires=("validated visual rows and original image descriptions",),
        writes=("source-explicit visual adjacency for explanatory descriptions",),
    ),
)
