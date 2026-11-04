# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Classify read attempt."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.images.refresh import can_refresh_image
from computer_use.interaction.read.acquisition.loading import (
    busy_nodes,
    refreshable,
    window_content_unexposed,
)
from computer_use.interaction.read.acquisition.model import (
    PURE,
    ClassifiedAttempt,
    RecheckedAttempt,
    Refresh,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def classify_read_attempt(attempt: RecheckedAttempt, control: Control) -> ClassifiedAttempt:
    """Select the next collection action from the scope recheck and component evidence.

    A first attempt may refresh a confirmed changed scope, stale image geometry, or eligible
    busy or positively unexposed content when budget remains. Return that decision or keep the
    collected components, annotating an unresolved scope change as a gap.
    """
    state, components = attempt.collected.state, attempt.collected.components
    target, current = attempt.target, attempt.current
    try:
        remaining_ms = cast("Callable[[], int]", control.remaining)()
    except TimeoutError:
        # Acquired components remain valid evidence at their capture interval.
        # Deadline exhaustion prevents optional recollection, not delivery.
        remaining_ms = 0
    if current != target:
        if state.plan.scope_managed:
            # The interface collector consumes this new directory and reads
            # exact members. Do not recollect a whole App or discard the parent
            # here; catalogue completeness is not a member-read prerequisite.
            return ClassifiedAttempt(replace(attempt, scope_confirmed=False), Refresh.KEEP)
        if attempt.scope_confirmed and state.attempt == 0 and remaining_ms > 0:
            return ClassifiedAttempt(attempt, Refresh.SCOPE)
        gap = ObservationGap(
            "scope",
            "observation_incomplete",
            "The observed view changed or could not be confirmed after collection; "
            "the returned components retain their original view.",
        )
        return ClassifiedAttempt(replace(attempt, scope_confirmed=False, gaps=attempt.gaps + (gap,)), Refresh.KEEP)
    matches_attempt = can_refresh_image(
        attempt=state.attempt,
        scope_confirmed=attempt.scope_confirmed,
        image=components.image,
        gaps=components.gaps,
        remaining=lambda: remaining_ms,
    )
    if matches_attempt:
        return ClassifiedAttempt(attempt, Refresh.IMAGE)
    limits = state.plan.limits
    matches_refreshable = (
        True
        if (
            state.attempt == 0
            and target.node is not None
            and refreshable(components.tree)
            and (
                busy_nodes(components.tree)
                or limits["fields"] is None
                and limits["max_depth"] is None
                and window_content_unexposed(components.tree)
            )
            and remaining_ms >= 400
        )
        else False
    )
    if matches_refreshable:
        return ClassifiedAttempt(attempt, Refresh.TREE)
    return ClassifiedAttempt(attempt, Refresh.KEEP)


stage = Pass.define(
    name="classify_read_attempt",
    function=classify_read_attempt,
    contract=Contract(RecheckedAttempt, ClassifiedAttempt, PURE),
)
if TYPE_CHECKING:
    from typing import Callable
