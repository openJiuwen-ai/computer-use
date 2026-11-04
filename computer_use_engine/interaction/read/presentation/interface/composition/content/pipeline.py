# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize complete form content from the prepared source and atomic plans."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.content.model import (
    FieldContentPlan,
    FieldContentRequest,
)
from computer_use.interaction.read.presentation.interface.composition.content.passes.plan_field_content import (
    stage as plan_field_content,
)
from computer_use.pipeline import Pipeline
from computer_use.pipeline.core import Step


class FieldContentPipeline(Pipeline[FieldContentRequest, FieldContentPlan]):
    """Select exact label/help slots and independent member disclosure before formatting."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[FieldContentRequest, FieldContentPlan]:
        return plan_field_content()
