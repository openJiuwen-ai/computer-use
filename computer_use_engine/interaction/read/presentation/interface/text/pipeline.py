# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Final text generation from prepared interface blocks."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.text.model import InterfaceTextRequest
from computer_use.interaction.read.presentation.interface.text.passes.format_blocks import stage as format_blocks
from computer_use.interaction.read.presentation.interface.text.passes.assemble_interface import (
    stage as assemble_interface,
)
from computer_use.interaction.read.presentation.records import RenderedTree
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class InterfaceTextPipeline(Pipeline[InterfaceTextRequest, RenderedTree]):
    """Serialize selected layouts and publish their source-accounted text.

    Content, grouping, real targets and disclosure choices are already frozen in
    the input. These stages only format that plan and retain complete atomic text
    for the existing contextual diff delivery.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceTextRequest, RenderedTree]:
        return Sequence(name="InterfaceTextPipeline", steps=[format_blocks(), assemble_interface()])
