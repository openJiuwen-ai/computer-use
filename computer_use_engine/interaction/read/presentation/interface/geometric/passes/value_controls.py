# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Associate readonly displayed values with their separate selector buttons."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.algorithms.value_controls import (
    associate_value_controls,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Keep a proved readonly value/selector pair in its existing labelled field."""
    return associate_value_controls(draft)


stage = Pass.define(
    name="GeometricValueControlPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete labelled fields and source-backed action geometry",),
        writes=("field membership with independent readonly value and selector targets",),
    ),
)
