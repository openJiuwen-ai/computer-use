from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.requests import ExecuteAction

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import OperationTarget
    from computer_use.session.state import AppState


@dataclass(frozen=True)
class ActionPlan:
    """Controller action paired with its App reconciliation and call-budget requirements.

    Attributes:
        request: Original API operation and target scope.
        state: App state needed for keyboard input or element-path reconciliation.
        reconcile: Whether to refresh the App's selected working-window scope before execution.
        remaining: Borrowed budget callback supplied by the reconciliation call scope.
        target: Native target used when the action reaches the write engine.
    """

    request: ExecuteAction
    state: AppState | None = None
    reconcile: bool = False
    remaining: Remaining | None = None
    target: OperationTarget | None = None
