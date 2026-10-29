# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import PlannedUnit, RoutePlan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def build_unit_command(plan: RoutePlan, control: Control) -> PlannedUnit:
    """Pair the selected route with its native command and original intent."""
    return PlannedUnit(plan, plan.command())


stage = Pass.define(
    name="build_unit_command",
    function=build_unit_command,
    contract=Contract(RoutePlan, PlannedUnit),
)
