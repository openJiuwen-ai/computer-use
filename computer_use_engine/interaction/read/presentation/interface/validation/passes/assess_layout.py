# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate source identity before classifying optional layout failures."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.model import LayoutAssessment
from computer_use.interaction.read.presentation.interface.validation.inspection import layout_issues, validate_sources
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> LayoutAssessment:
    """Validate source identity before classifying optional layout failures."""
    return LayoutAssessment(draft, layout_issues(validate_sources(draft)))


stage = Pass.define(
    name="assess_layout", function=execute, contract=Contract(InterfaceDraft, LayoutAssessment, {Effect.PURE})
)
