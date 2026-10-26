"""Complete empty input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import AttemptOutcome, ResolvedWrite
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    pass


def complete_empty(resolved: ResolvedWrite, _: object) -> AttemptOutcome:
    """Return a verified receipt for empty insertion or paste text."""
    return AttemptOutcome(
        resolved,
        completed=dict(
            target=resolved.target.facts.target_label,
            status="verified",
            detail="Empty text; no input or preparation was needed.",
        ),
    )


stage = Pass.define(
    name="complete_empty_input",
    function=complete_empty,
    contract=Contract(ResolvedWrite, AttemptOutcome),
)
