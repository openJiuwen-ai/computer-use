"""Account fields actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.accounting.fields import account_fields
from computer_use.interaction.read.presentation.model import PURE, FieldAccounting
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import RenderedTree, SnapshotFacts
    from computer_use.pipeline.core import Control


def create(*, branch: BranchFacts, snapshot: SnapshotFacts) -> Callable[[RenderedTree, Control], FieldAccounting]:
    def fields(rendered: RenderedTree, control: Control) -> FieldAccounting:
        """Record represented native fields/actions and return the values requiring supplements."""
        residuals, accounting = account_fields(
            branch.members, snapshot.refs, snapshot.fields, branch.native_nodes, rendered
        )
        rendered["factAccounting"] = accounting
        return FieldAccounting(rendered, residuals)

    return fields


stage = Pass.factory(
    name="account_fields_actions",
    factory=create,
    contract=Contract(dict, FieldAccounting, PURE, requires=("actual output", "native fields and actions")),
)
