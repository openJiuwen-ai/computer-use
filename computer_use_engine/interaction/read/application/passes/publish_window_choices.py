"""Publish window choices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.application.model import AppReadResult
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.model import WindowSelectionRequired
    from computer_use.pipeline.core import Control
    from computer_use.session.state import AppState
    from computer_use.types import Observation


def create(
    *, publish_choices: Callable[[object, AppState, WindowSelectionRequired, bool], Observation]
) -> Callable[[AppReadResult, Control], Observation]:

    def choices(value: AppReadResult, control: Control) -> Observation:
        """Publish the ambiguous application read's current window choices through the session
        owner.
        """
        return publish_choices(
            value.source.request.owner, value.source.state, value.observed, value.source.request.full
        )

    return choices


stage = Pass.factory(
    name="publish_window_choices",
    factory=create,
    contract=Contract(AppReadResult, object, {Effect.RESOURCE}),
)
