# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share one complete native ID prefix in compact full root output."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.identifier_prefixes import pack_identifier_prefixes
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Pack generated ID fields while retaining original node bodies and bindings."""
    lines = pack_identifier_prefixes(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_identifier_prefix", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_identifier_prefix",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete native identifier sources", "unchanged generated ID fields"),
        writes=("packed lines",),
    ),
)
