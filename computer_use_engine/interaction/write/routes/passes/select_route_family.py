# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import RoutePlan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable, Mapping

    from computer_use.pipeline.core import Control


def create(*, family: Mapping[str, str]) -> Callable[[RoutePlan, Control], str]:
    def select_route_family(plan: RoutePlan, control: Control) -> str:
        """Look up the execution family for the selected primitive unit."""
        return family[plan.primitive.unit]

    return select_route_family


stage = Pass.factory(
    name="select_route_family",
    factory=create,
    contract=Contract(RoutePlan, str),
)
