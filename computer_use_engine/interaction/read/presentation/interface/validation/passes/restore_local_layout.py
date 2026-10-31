# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Restore only closed source scopes selected by the layout gate."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.model import LayoutAssessment
from computer_use.interaction.read.presentation.interface.validation.recovery import recover_layout
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(assessment: LayoutAssessment, control: Control) -> InterfaceDraft:
    """Restore only closed source scopes selected by the layout gate."""
    return recover_layout(assessment)


stage = Pass.define(
    name="restore_local_layout", function=execute, contract=Contract(LayoutAssessment, InterfaceDraft, {Effect.PURE})
)
