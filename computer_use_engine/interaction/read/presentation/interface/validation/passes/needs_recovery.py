# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose local recovery only for explicit optional-layout failures."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.validation.model import LayoutAssessment

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(assessment: LayoutAssessment, control: Control) -> bool:
    """Choose local recovery only for explicit optional-layout failures."""
    return bool(assessment.issues)


stage = Pass.define(name="needs_recovery", function=execute, contract=Contract(LayoutAssessment, bool, {Effect.PURE}))
