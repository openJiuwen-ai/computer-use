# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share exact unknown input fields within their proven table."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.input_fields import pack_input_fields
from computer_use.interaction.read.presentation.rendering.layout.table_entries import expand_table_entries
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.protocol.records import Record


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Publish one table's shared unknown Click/Input pair with each original reference."""
    packed = pack_input_fields(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += ({"name": "group_input_fields", "beforeLines": len(frame.lines), "afterLines": len(packed.lines)},)
    original = frame.finalize
    finalize = original
    if packed.replacements and original is not None:

        def finalize(lines: list[str], stages: list[Record]) -> RenderedTree:
            result = original(lines, stages)
            checked = expand_table_entries(lines)
            if checked == packed.lines and result.get("text") == "\n".join(lines):
                result["inputFieldsReplacements"] = [asdict(item) for item in packed.replacements]
            return result

    return replace(frame, lines=packed.lines, records=records, finalize=finalize)


stage = Pass.define(
    name="group_input_fields",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("exact generated input fields and table containment",),
        writes=("packed lines", "inputFieldsReplacements publication coverage"),
    ),
)
