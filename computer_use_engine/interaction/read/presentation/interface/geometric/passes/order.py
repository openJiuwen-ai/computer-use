# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply local reading constraints while preserving native logical order."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.algorithms.order_regions import apply as order_regions
from computer_use.interaction.read.presentation.interface.geometric.algorithms.order import order_visual_rows
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Apply local reading constraints while preserving native logical order."""
    draft = order_regions(draft)
    draft = order_visual_rows(draft)
    return draft


stage = Pass.define(
    name="GeometricOrderPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable captured facts and explicit source membership",),
        writes=("local geometric order plan",),
    ),
)
