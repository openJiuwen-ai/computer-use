from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.requests import ReadApp

if TYPE_CHECKING:
    from computer_use.interaction.read.model import AppObservation, WindowSelectionRequired
    from computer_use.session.state import AppState


@dataclass(frozen=True)
class AppReadState:
    """Application read request paired with its session-owned observation state.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        state: Session-owned observation state for the requested App or Window handle.
    """

    request: ReadApp
    state: AppState


@dataclass(frozen=True)
class AppReadResult:
    """Acquired application evidence or the choices needed to disambiguate its windows.

    Attributes:
        source: Original read request and its session observation state.
        observed: Collected application evidence or the window-selection response.
        choices: Whether the observation requires presenting window choices.
    """

    source: AppReadState
    observed: AppObservation | MenuAppObservation | WindowSelectionRequired
    choices: bool = False


if TYPE_CHECKING:
    from computer_use.interaction.read.model import MenuAppObservation
