"""Classify read attempt."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.components.support import ObservationGap
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
    initializing/busy content when budget remains. Return that decision or keep the
    collected components, annotating an unresolved scope change as a gap.
    """
    state, components = attempt.collected.state, attempt.collected.components
    target, current = attempt.target, attempt.current
    if current != target:
        if attempt.scope_confirmed and state.attempt == 0:
            return ClassifiedAttempt(attempt, Refresh.SCOPE)
        gap = ObservationGap(
            "scope",
            "observation_incomplete",
            "The observed view changed or could not be confirmed after collection; "
            "the returned components retain their original view.",
            "read again before acting on the current view",
        )
        return ClassifiedAttempt(replace(attempt, scope_confirmed=False, gaps=attempt.gaps + (gap,)), Refresh.KEEP)
    if (
        state.attempt == 0
        and attempt.scope_confirmed
        and components.image is None
        and any(gap.component == "image" and gap.code == "stale_target" for gap in components.gaps)
        and cast("Callable[[], int]", control.remaining)() >= 400
    ):
        return ClassifiedAttempt(attempt, Refresh.IMAGE)
    limits = state.plan.limits
    if (
        state.attempt == 0
        and target.node is not None
        and refreshable(components.tree)
        and (
            state.initializing
            or busy_nodes(components.tree)
            or limits["fields"] is None
            and limits["max_depth"] is None
            and window_content_unexposed(components.tree)
        )
        and cast("Callable[[], int]", control.remaining)() >= 400
    ):
        return ClassifiedAttempt(attempt, Refresh.TREE)
    return ClassifiedAttempt(attempt, Refresh.KEEP)


stage = Pass.define(
    name="classify_read_attempt",
    function=classify_read_attempt,
    contract=Contract(RecheckedAttempt, ClassifiedAttempt, PURE),
)
if TYPE_CHECKING:
    from typing import Callable
