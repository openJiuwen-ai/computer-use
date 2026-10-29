# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Readrefreshgate."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import (
    PURE,
    AcquiredObservation,
    ClassifiedAttempt,
    ReadRunState,
    Refresh,
)
from computer_use.pipeline import Continue, Contract, Done, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def read_refresh_gate(
    classified: ClassifiedAttempt, control: Control
) -> Continue[ReadRunState] | Done[AcquiredObservation]:
    """Complete acquisition or prepare the selected bounded refresh attempt.

    A wait failure becomes a final observation gap. Otherwise return the collected
    observation when keeping components, or advance the attempt with its selected scope and
    refresh mode.
    """
    attempt = classified.rechecked
    state, components = attempt.collected.state, attempt.collected.components
    gaps = attempt.gaps
    if classified.wait_gap is not None:
        components = replace(components, gaps=components.gaps + (classified.wait_gap,))
        return Done(
            AcquiredObservation(
                state.plan, attempt.target, attempt.directory, components, gaps + components.gaps, False
            )
        )
    if classified.refresh is Refresh.KEEP:
        return Done(
            AcquiredObservation(
                state.plan,
                attempt.target,
                attempt.directory,
                components,
                gaps + components.gaps,
                attempt.scope_confirmed,
            )
        )
    # Repeat consumes this immutable next state. There is one owner and one
    # shared allowance, regardless of the reason for a refresh.
    return Continue(
        ReadRunState(
            state.plan,
            attempt.current if classified.refresh is Refresh.SCOPE else attempt.target,
            attempt.directory,
            state.initializing,
            attempt=state.attempt + 1,
            refresh_tree=classified.refresh is Refresh.TREE,
            components=components,
            gaps=gaps,
        )
    )


stage = Pass.define(
    name="ReadRefreshGate",
    function=read_refresh_gate,
    contract=Contract(ClassifiedAttempt, object, PURE),
)
