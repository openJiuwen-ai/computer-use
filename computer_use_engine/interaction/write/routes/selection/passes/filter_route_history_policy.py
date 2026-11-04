# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Filter route history policy."""

from computer_use.interaction.write.routes.selection.model import CandidateSet, EligibleRoutes
from computer_use.interaction.write.routes.selection.unit_choice import eligible_unit_routes
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def filter_route_history_policy(value: CandidateSet) -> EligibleRoutes:
    """Filter candidates using the shared pure eligibility rules."""
    return eligible_unit_routes(value)


def execute(value: CandidateSet, control: Control) -> EligibleRoutes:
    """Filter candidates by history and required editing, enabled and geometry evidence."""
    return filter_route_history_policy(value)


stage = Pass.define(
    name="filter_route_history_policy",
    function=execute,
    contract=Contract(CandidateSet, EligibleRoutes, requires=("read-only route history", "editing evidence")),
)
