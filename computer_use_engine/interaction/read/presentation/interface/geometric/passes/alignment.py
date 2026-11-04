# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discover row bands and shared alignments before selecting regions or fields."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.candidates import collect_scope_plans
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Index position-based candidates while keeping source fields and order intact."""
    return collect_scope_plans(draft)


stage = Pass.define(
    name="GeometricAlignmentPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("whole source-backed units and comparable bounds",),
        writes=("immutable local row and alignment candidates",),
    ),
)
