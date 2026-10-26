"""Select write scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.request.model import ActionPlan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    pass


def execute(plan: ActionPlan, _: object) -> bool:
    """Select whether this action needs App-scope reconciliation."""
    return plan.reconcile


stage = Pass.define(
    name="select_write_scope",
    function=execute,
    contract=Contract(ActionPlan, bool),
)
