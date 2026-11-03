# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Materialize action result."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.request.model import ObservedAction
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control
    from computer_use.types import ActionResult


def create(*, action_result: Callable[..., ActionResult]) -> Callable[[ObservedAction, Control], ActionResult]:

    def result(outcome: ObservedAction, control: Control) -> ActionResult:
        """Convert the engine receipt into the public ActionResult."""
        if outcome.receipt is None:
            raise ValueError("A failed action must preserve its original exception, not create a receipt")
        return action_result(outcome.receipt, observation=outcome)

    return result


stage = Pass.factory(
    name="materialize_action_result",
    factory=create,
    contract=Contract(ObservedAction, object, {Effect.RESOURCE}),
)
