# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select full structured delivery before text generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.diff.formatting import DeltaPlan
from computer_use.interaction.read.delivery.diff.model import PURE, DeliverySelection, PlannedDelivery
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: DeliverySelection, control: Control) -> PlannedDelivery:
    """Retain the observation with a complete-view decision."""
    return PlannedDelivery(value.observation, DeltaPlan())


stage = Pass.define(
    name="plan_full_delivery", function=execute, contract=Contract(DeliverySelection, PlannedDelivery, PURE)
)
