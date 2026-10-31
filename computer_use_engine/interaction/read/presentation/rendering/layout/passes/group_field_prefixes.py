# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share complete caption and URL prefixes within one original root."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.field_prefixes import pack_field_prefixes
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Publish reversible suffixes and the exact native fields they represent."""
    packed = pack_field_prefixes(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += ({"name": "group_field_prefixes", "beforeLines": len(frame.lines), "afterLines": len(packed.lines)},)
    return replace(frame, lines=packed.lines, records=records, field_prefix_replacements=packed.replacements)


stage = Pass.define(
    name="group_field_prefixes",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete native scalar fields", "proven generated field spans"),
        writes=("packed lines", "field prefix destinations"),
    ),
)
