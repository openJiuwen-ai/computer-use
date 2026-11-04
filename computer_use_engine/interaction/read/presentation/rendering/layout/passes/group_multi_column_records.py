# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lay out complete multi-cell rows while retaining each object and its fields."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.multi_column_records import pack_multi_column_layout
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Share repeated row and column labels in complete multi-column records."""
    packed = pack_multi_column_layout(frame)
    lines = packed.lines
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_multi_column_records", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records, table_layouts=packed.tables)


stage = Pass.define(
    name="group_multi_column_records",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete generated multi-cell records",),
        writes=("packed lines", "generated table introductions and ordered rows"),
    ),
)
