# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Write replan gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.diagnostics import record_click_decision
from computer_use.interaction.write.model import AttemptOutcome
from computer_use.interaction.write.progress import classify_write_progress
from computer_use.pipeline import Continue, Contract, Done, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.pipeline.core import Control


def create(*, state: WriteRunState) -> Callable[[AttemptOutcome, Control], Continue | Done]:
    def replan(outcome: AttemptOutcome, control: Control) -> Continue | Done:
        """Classify progress as a terminal result or another resolution attempt.

        Return Done with the completion or failure decision, or Continue with the
        original bound write. The parent loop applies the decision to shared history.
        """
        progress = state.view()
        decision = classify_write_progress(outcome, progress)
        record_click_decision(control, outcome, decision, progress, len(state.steps))
        if decision.kind in ("complete", "stop"):
            return Done(decision.receipt, decision)
        return Continue(outcome.resolved.bound, decision)

    return replan


stage = Pass.factory(
    name="WriteReplanGate",
    factory=create,
    contract=Contract(AttemptOutcome, Continue | Done),
)
