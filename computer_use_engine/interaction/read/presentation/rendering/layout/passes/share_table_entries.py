# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share identical generated table-reading instructions within one root."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.table_entries import pack_table_entries
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.protocol.records import Record


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Publish one root's common Entries sentence while keeping each table's own fields."""
    packed = pack_table_entries(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += ({"name": "share_table_entries", "beforeLines": len(frame.lines), "afterLines": len(packed.lines)},)
    original = frame.finalize
    finalize = original
    if packed.replacements and original is not None:

        def finalize(lines: list[str], stages: list[Record]) -> RenderedTree:
            result = original(lines, stages)
            checked = lines
            if checked == packed.lines and result.get("text") == "\n".join(lines):
                result["tableEntriesReplacements"] = [asdict(item) for item in packed.replacements]
            return result

    return replace(frame, lines=packed.lines, records=records, finalize=finalize)


stage = Pass.define(
    name="share_table_entries",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("generated table introductions and ordered rows",),
        writes=("packed lines", "tableEntriesReplacements publication coverage"),
    ),
)
