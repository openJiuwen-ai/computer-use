# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Project complete visual rows into compact shared columns."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.algorithms.columns import plan_columns
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Freeze column slots and alignment without generating text or rebinding targets."""
    return plan_columns(draft)


stage = Pass.define(
    name="GeometricProjectionPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("completed local rows and unchanged native references",),
        writes=("compact column slots and alignment",),
    ),
)
