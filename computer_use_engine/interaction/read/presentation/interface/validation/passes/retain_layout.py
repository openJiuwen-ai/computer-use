# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep the original immutable draft when all layout contracts hold."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.model import LayoutAssessment

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(assessment: LayoutAssessment, control: Control) -> InterfaceDraft:
    """Keep the original immutable draft when all layout contracts hold."""
    return assessment.draft


stage = Pass.define(
    name="retain_layout", function=execute, contract=Contract(LayoutAssessment, InterfaceDraft, {Effect.PURE})
)
