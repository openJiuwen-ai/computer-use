# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare structured snapshots without accessing or formatting their text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff
from computer_use.interaction.read.delivery.diff.model import PURE, DeliverySelection, PlannedDelivery
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: DeliverySelection, control: Control) -> PlannedDelivery:
    """Plan changed nodes and truthful missing-reference descriptions from retained facts."""
    observation, previous = value.observation, value.previous

    plan = plan_document_diff(
        previous.get("document"), observation.get("document"), previous["refs"], observation["refs"],
        coverage=(previous.get("coverage"), observation.get("coverage")),
    )
    return PlannedDelivery(observation, plan)


stage = Pass.define(
    name="plan_observation_diff", function=execute, contract=Contract(DeliverySelection, PlannedDelivery, PURE)
)
