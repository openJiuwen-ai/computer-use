"""Prepare element lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.query.model import FindPlan
from computer_use.interaction.requests import FindElement
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control
    from computer_use.session.state import AppState


def create(
    *, query_root: Callable[[object], Binding | None], state_of: Callable[[object], AppState]
) -> Callable[[FindElement, Control], FindPlan]:
    """Bind access to a registered handle's query root and session state.

    Args:
        query_root: Resolve the handle's observed native query binding.
        state_of: Retrieve the session-owned state for that handle.
    """

    def prepare(request: FindElement, control: Control) -> FindPlan:
        """Pair the lookup request with its owner's observed root and App state."""
        target = query_root(request.owner)
        return FindPlan(request, state_of(request.owner), target)

    return prepare


stage = Pass.factory(
    name="prepare_element_lookup",
    factory=create,
    contract=Contract(FindElement, FindPlan, {Effect.RESOURCE}),
)
