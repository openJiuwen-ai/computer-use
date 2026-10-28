# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reuse each object's literal Description suffix in its complete URL."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.local_field_suffixes import (
    pack_local_field_suffixes,
)
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Publish one-hop URL expressions and retain their exact local field sources."""
    packed = pack_local_field_suffixes(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (
            {"name": "share_local_field_suffixes", "beforeLines": len(frame.lines), "afterLines": len(packed.lines)},
        )
    return replace(frame, lines=packed.lines, records=records, local_field_suffix_replacements=packed.replacements)


stage = Pass.define(
    name="share_local_field_suffixes",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete local native Description and URL", "proven generated scalar slots"),
        writes=("packed lines", "local URL field destinations"),
    ),
)
