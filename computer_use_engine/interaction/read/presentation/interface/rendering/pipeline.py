# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Format structured interface decisions at the final text boundary."""

from __future__ import annotations

from computer_use.pipeline import Pipeline
from computer_use.pipeline.core import Step
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from .passes.format_blocks import stage as format_blocks


class InterfaceFormattingPipeline(Pipeline[LayoutFrame, LayoutFrame]):
    """Arrange exact element fields using the already prepared region and record plan."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[LayoutFrame, LayoutFrame]:
        return format_blocks()
