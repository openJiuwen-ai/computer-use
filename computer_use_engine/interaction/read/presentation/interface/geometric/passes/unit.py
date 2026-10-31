# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Classify real controls and content while retaining their independent targets."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.algorithms.classify_units import (
    apply as classify_units,
)
from computer_use.interaction.read.presentation.interface.geometric.algorithms.units import compose_control_units
from computer_use.interaction.read.presentation.interface.geometric.frontier import expand_transparent_frontier
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Classify real controls and content while retaining their independent targets."""
    draft = classify_units(draft)
    draft = compose_control_units(draft)
    draft = expand_transparent_frontier(draft)
    return draft


stage = Pass.define(
    name="GeometricUnitPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable captured facts and explicit source membership",),
        writes=("local geometric unit plan",),
    ),
)
