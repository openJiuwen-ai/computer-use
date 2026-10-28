# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select source-backed grid, table and card patterns for native collections."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.patterns.composition import apply_patterns
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Organize captured collection members as complete visual items and native rows."""
    return apply_patterns(draft)


stage = Pass.define(
    name="GeometricPatternPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable source identities, native collection membership and geometry",),
        writes=("selected local collection patterns and source-preserving display blocks",),
    ),
)
