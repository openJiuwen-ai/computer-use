# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish root-local role spellings while retaining original node records."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.role_labels import pack_role_labels
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Replace proven role slots and record their exact source destinations."""
    packed = pack_role_labels(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += ({"name": "group_role_labels", "beforeLines": len(frame.lines), "afterLines": len(packed.lines)},)
    return replace(frame, lines=packed.lines, records=records, role_label_replacements=packed.replacements)


stage = Pass.define(
    name="group_role_labels",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("complete native role spellings", "unique generated role slots in one root"),
        writes=("packed lines", "exact role spelling destinations"),
    ),
)
