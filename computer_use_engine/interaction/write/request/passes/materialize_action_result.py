# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Materialize action result."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult


def create(*, action_result: Callable[[Record], ActionResult]) -> Callable[[Record, Control], ActionResult]:

    def result(receipt: Record, control: Control) -> ActionResult:
        """Convert the engine receipt into the public ActionResult."""
        return action_result(receipt)

    return result


stage = Pass.factory(
    name="materialize_action_result",
    factory=create,
    contract=Contract(dict, object, {Effect.RESOURCE}),
)
