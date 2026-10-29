# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep the explicitly addressed target for direct execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def direct_target(frame: ResolutionFrame, control: Control) -> ResolutionFrame:
    """Carry the explicitly addressed target into native fact inspection."""
    return frame


stage = Pass.define(
    name="DirectTargetPipeline",
    function=direct_target,
    contract=Contract(ResolutionFrame, ResolutionFrame),
)
