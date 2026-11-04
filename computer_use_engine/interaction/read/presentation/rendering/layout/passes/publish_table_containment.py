# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish column-edge evidence only for the final delivered table layout."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.table_containment import published_table_columns
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.protocol.records import Record


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Attach exact column destinations without changing node text or source bindings."""
    finalize = frame.finalize
    if frame.table_sources and finalize is not None:
        original = finalize

        def finalize(lines: list[str], records: list[Record]) -> RenderedTree:
            result = original(lines, records)
            # The evidence describes the lines actually finalized, including any
            # preceding layout transformations; it never relies on an earlier draft.
            columns = published_table_columns(replace(frame, lines=lines))
            if columns and result["text"] == "\n".join(lines):
                result["publishedTableColumns"] = [column.to_record() for column in columns]
            return result

    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="publish_table_containment", beforeLines=len(frame.lines), afterLines=len(frame.lines)),)
    return replace(frame, finalize=finalize, records=records)


stage = Pass.define(
    name="publish_table_containment",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete native column edges", "actual public row/cell layout"),
        writes=("published table-column relationship evidence",),
    ),
)
