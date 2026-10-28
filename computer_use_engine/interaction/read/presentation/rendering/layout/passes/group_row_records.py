# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pack complete row/single-cell records into an explicit four-column table."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.row_records import pack_row_records
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Preserve independent node records while sharing repeated table fields."""
    lines = pack_row_records(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_row_records", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_row_records",
    function=execute,
    contract=Contract(
        LayoutFrame, LayoutFrame, requires=("complete generated row and cell records",), writes=("packed lines",)
    ),
)
