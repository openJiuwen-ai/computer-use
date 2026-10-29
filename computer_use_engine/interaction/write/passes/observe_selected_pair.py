# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe selected pair."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolvedWrite
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.pipeline.core import Control


def create(*, state: WriteRunState) -> Callable[[ResolvedWrite, Control], ResolvedWrite]:
    def observe_pair(resolved: ResolvedWrite, _: object) -> ResolvedWrite:
        """Record the selected actuator/receiver pair for this write's stability bound."""
        state.attempts.observe(resolved.target.facts)
        return resolved

    return observe_pair


stage = Pass.factory(
    name="observe_selected_pair",
    factory=create,
    contract=Contract(ResolvedWrite, ResolvedWrite, {Effect.RESOURCE}, writes=("attempts",)),
)
