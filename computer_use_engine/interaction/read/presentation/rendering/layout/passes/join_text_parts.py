# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.packing import apply_layout
from computer_use.interaction.read.presentation.rendering.layout.text_parts import compact_text_parts

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import FormatField, NodeView
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def join_text_parts(
    lines: list[str], roots: Sequence[NodeView], context: Record, format_field: FormatField
) -> list[str]:
    return compact_text_parts(lines, roots, context.get("focusedRef"), format_field)


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Pack adjacent passive text entries and return the updated layout frame and optional stage counts."""
    return apply_layout(join_text_parts, frame)
