# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply immutable block choices to source-accounted element fields."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from ..formatting import format_interface


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Format the prepared interface while retaining atomic nodes for incremental delivery."""
    return format_interface(frame)


stage = Pass.define(
    name="format_interface_blocks",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        {Effect.PURE},
        requires=("immutable interface plan", "exact element field emissions"),
        writes=("interface text layout", "incremental region context"),
    ),
)
