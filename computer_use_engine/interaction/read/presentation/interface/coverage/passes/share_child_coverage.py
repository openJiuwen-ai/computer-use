# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan equivalent direct-child collection hints without altering their source atoms."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.coverage.model import CoveragePlan, CoverageRequest
from computer_use.interaction.read.presentation.interface.coverage.planning import plan_coverage
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: CoverageRequest, control: Control) -> CoveragePlan:
    """Capture same-scope repeated coverage at its nearest displayed ancestor."""
    return plan_coverage(request)


stage = Pass.define(
    name="share_interface_child_coverage",
    function=execute,
    contract=Contract(
        CoverageRequest,
        CoveragePlan,
        {Effect.PURE},
        requires=("original collection counts and block ancestor paths",),
        writes=("whole-view shared coverage declarations",),
    ),
)
