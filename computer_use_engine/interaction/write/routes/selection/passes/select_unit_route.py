# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose native-unit preparation and delivery in one background-first policy."""

from computer_use.interaction.write.routes.selection.model import EligibleRoutes, RouteOptions
from computer_use.interaction.write.routes.selection.unit_choice import select_unit_route
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(eligible: EligibleRoutes, control: Control) -> RouteOptions:
    """Apply the same pure strategy used to qualify optional editor discovery."""
    return select_unit_route(eligible)


stage = Pass.define(
    name="select_unit_route",
    function=execute,
    contract=Contract(EligibleRoutes, RouteOptions, requires=("route-local admission", "read-only attempts")),
)
