# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Group independently bound local controls into geometric reading rows."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.algorithms.rows import group_visual_rows
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Expose same-row relationships inside fields and ordinary local scopes."""
    return group_visual_rows(draft)


stage = Pass.define(
    name="GeometricRowPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete scope-compatible bounds",),
        writes=("independent member rows",),
    ),
)
