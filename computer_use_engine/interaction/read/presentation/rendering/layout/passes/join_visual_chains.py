# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pack complete node lines only along spatially marked containment edges."""

from __future__ import annotations

from collections.abc import Sequence

from computer_use.interaction.read.presentation.records import FormatField, NodeView
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.packing import apply_layout
from computer_use.interaction.read.presentation.rendering.layout.visual_chains import pack_visual_chains
from computer_use.pipeline.core import Control
from computer_use.protocol.records import Record


def join_visual_chains(
    lines: list[str], roots: Sequence[NodeView], context: Record, format_field: FormatField
) -> list[str]:
    """Retain every source body while packing proven single-control wrappers."""
    return pack_visual_chains(lines, roots, context)


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    return apply_layout(join_visual_chains, frame)
