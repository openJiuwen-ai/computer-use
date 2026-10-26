"""Compare structured snapshots without accessing or formatting their text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.coverage import confirmsRemoval
from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff
from computer_use.interaction.read.delivery.diff.model import PURE, DeliverySelection, PlannedDelivery
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: DeliverySelection, control: Control) -> PlannedDelivery:
    """Plan changed nodes and truthful missing-reference descriptions from retained facts."""
    observation, previous = value.observation, value.previous
    not_observed = {
        ref
        for ref in previous["refs"]
        if ref not in observation["refs"]
        and not confirmsRemoval(previous.get("coverage"), observation.get("coverage"), ref)
    }
    plan = plan_document_diff(
        previous.get("document"), observation.get("document"), previous["refs"], observation["refs"], not_observed
    )
    return PlannedDelivery(observation, plan)


stage = Pass.define(
    name="plan_observation_diff", function=execute, contract=Contract(DeliverySelection, PlannedDelivery, PURE)
)
