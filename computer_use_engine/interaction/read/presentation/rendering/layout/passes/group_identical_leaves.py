# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pack repeated sibling leaf bodies while retaining each numbered element."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.identical_leaves import pack_identical_leaves
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Apply reversible leaf shorthand after other compact layout transforms."""
    lines = pack_identical_leaves(frame.lines, frame.roots, frame.context)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_identical_leaves", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_identical_leaves",
    function=execute,
    contract=Contract(LayoutFrame, LayoutFrame, requires=("complete rendered leaf bodies",), writes=("packed lines",)),
)
