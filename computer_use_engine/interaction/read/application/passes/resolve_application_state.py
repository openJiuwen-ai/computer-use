"""Resolve application state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.application.model import AppReadState
from computer_use.interaction.requests import ReadApp
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control
    from computer_use.session.state import AppState


def create(*, state_of: Callable[[object], AppState]) -> Callable[[ReadApp, Control], AppReadState]:

    def state(request: ReadApp, control: Control) -> AppReadState:
        """Pair the application request with the observation state owned by its handle."""
        return AppReadState(request, state_of(request.owner))

    return state


stage = Pass.factory(
    name="resolve_application_state",
    factory=create,
    contract=Contract(ReadApp, AppReadState, {Effect.RESOURCE}),
)
