# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share complete ancestor text while preserving each child field's own source."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.protocol.records import Record

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.text_slices import (
    pack_exact_text_slices,
    record_text_slices,
)
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Pack field text and retain its exact public destination for final accounting."""
    layout = pack_exact_text_slices(frame)
    finalize = frame.finalize
    if layout.slices and finalize is not None:
        original = finalize
        sources = frame.text_slice_sources

        def finalize(lines: list[str], records: list[Record]) -> RenderedTree:
            return record_text_slices(original(lines, records), layout, sources)

    records = frame.records
    if frame.context.get("recordStages"):
        records += (
            dict(
                name="share_exact_text_slices",
                beforeLines=len(frame.lines),
                afterLines=len(layout.lines),
                sharedFields=len(layout.slices),
            ),
        )
    return replace(frame, lines=layout.lines, records=records, finalize=finalize)


stage = Pass.define(
    name="share_exact_text_slices",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete original text fields and containment", "fully public ancestor anchors"),
        writes=("packed lines", "public field destinations"),
    ),
)
