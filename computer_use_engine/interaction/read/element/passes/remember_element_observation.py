"""Remember element observation and borrow its one resolved App state."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.element.model import ElementReadPlan
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control
    from computer_use.session.state import AppState


def create(
    *,
    remember_snapshot: Callable[[AppState, Binding, SemanticSnapshot], None],
    state_of: Callable[[object], AppState],
) -> Callable[[ElementReadPlan, Control], ElementReadPlan]:

    def remember(plan: ElementReadPlan, control: Control) -> ElementReadPlan:
        """Retain the selected element tree in session state and attach that state to the read
        plan.
        """
        state = state_of(plan.request.owner)
        remember_snapshot(state, plan.selected["binding"], cast("SemanticSnapshot", plan.tree))
        return replace(plan, state=state)

    return remember


stage = Pass.factory(
    name="remember_element_observation",
    factory=create,
    contract=Contract(ElementReadPlan, ElementReadPlan, {Effect.RESOURCE}),
)
