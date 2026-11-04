# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share generated role/state prefixes in compact full leaf lists."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.leaf_headers import pack_leaf_headers
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Add local shared headings while keeping all element records unchanged."""
    lines = pack_leaf_headers(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_leaf_headers", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_leaf_headers",
    function=execute,
    contract=Contract(
        LayoutFrame, LayoutFrame, requires=("complete generated leaf prefixes",), writes=("packed lines",)
    ),
)
