"""Prepare write scope."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.native.scopes import window_binding
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control
    from computer_use.session.state import AppState


def create(*, state_of: Callable[[object], AppState]) -> Callable[[ExecuteAction, Control], ActionPlan]:

    def prepare(request: ExecuteAction, control: Control) -> ActionPlan:
        """Determine the working-window reconciliation needed by this action.

        Consult App state for keyboard input or a scoped element path. Return an
        ActionPlan carrying that state, the original target, and the reconciliation flag.
        """
        state = state_of(request.owner) if request.app_keyboard or request.path is not None else None
        reconcile = (
            state is not None
            and state.selection is not None
            and (
                request.app_keyboard
                or (
                    state.observation is not None and request.target == cast("ObservedWindow", state.observation).target
                )
            )
            and (window_binding(request.target) != state.selection.root)
        )
        return ActionPlan(request, state, reconcile, target=request.target)

    return prepare


stage = Pass.factory(
    name="prepare_write_scope",
    factory=create,
    contract=Contract(ExecuteAction, ActionPlan, {Effect.RESOURCE}),
)
if TYPE_CHECKING:
    from computer_use.interaction.records import ObservedWindow
