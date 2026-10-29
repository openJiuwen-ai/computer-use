# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.empty_values import compact_empty_values
from computer_use.interaction.read.presentation.rendering.layout.packing import apply_layout

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import FormatField, NodeView
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def group_empty_values(
    lines: list[str], roots: Sequence[NodeView], context: Record, format_field: FormatField
) -> list[str]:
    return compact_empty_values(lines, roots, context.get("focusedRef"))


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Group eligible empty-value entries and return the updated layout frame and optional stage counts."""
    return apply_layout(group_empty_values, frame)
