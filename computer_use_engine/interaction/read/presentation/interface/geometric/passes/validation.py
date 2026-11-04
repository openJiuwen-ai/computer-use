# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check source conservation and classify optional layout failures."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.validation.inspection import layout_issues, validate_sources
from computer_use.interaction.read.presentation.interface.validation.model import LayoutAssessment
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> LayoutAssessment:
    """Reject identity changes and return local format issues to the unique recovery gate."""
    return LayoutAssessment(validate_sources(draft), layout_issues(draft))


stage = Pass.define(
    name="GeometricValidationPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        LayoutAssessment,
        {Effect.PURE},
        requires=("complete source and layout plan",),
        writes=("typed layout assessment",),
    ),
)
