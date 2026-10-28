# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share complete identical descriptions within one public root."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.shared_field_descriptions import (
    pack_shared_descriptions,
)
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Publish explicit Description member lists and preserve their field sources."""
    packed = pack_shared_descriptions(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (
            {"name": "group_field_descriptions", "beforeLines": len(frame.lines), "afterLines": len(packed.lines)},
        )
    return replace(frame, lines=packed.lines, records=records, shared_description_replacements=packed.replacements)


stage = Pass.define(
    name="group_field_descriptions",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete same-root native descriptions", "exact adjacent public URL slots"),
        writes=("packed lines", "shared Description field destinations"),
    ),
)
