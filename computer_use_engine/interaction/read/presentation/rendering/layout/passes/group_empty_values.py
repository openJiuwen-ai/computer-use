# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share source-qualified empty cells without parsing rendered field wording."""

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.empty_values import compact_empty_values
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    lines = compact_empty_values(frame.lines, frame.empty_value_lines)
    records = frame.records
    if frame.context.get("recordStages"):
        records += ({"name": "group_empty_values", "beforeLines": len(frame.lines), "afterLines": len(lines)},)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_empty_values",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("source-qualified cell coordinates", "complete generated field slots"),
        writes=("packed lines", "layout diagnostics"),
    ),
)
