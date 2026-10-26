"""Assign menu window references."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.menu.model import (
    RESOURCE,
    CollectedMenuObservation,
    MenuTextDraft,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[CollectedMenuObservation, Control], MenuTextDraft]:
    def number_windows(value: CollectedMenuObservation, control: Control) -> MenuTextDraft:
        """Create a menu text draft with aliases for the rechecked surface catalogue."""
        return MenuTextDraft(value, services.window_aliases(value.directory))

    return number_windows


stage = Pass.factory(
    name="assign_menu_window_references",
    factory=create,
    contract=Contract(CollectedMenuObservation, MenuTextDraft, RESOURCE, writes=("session window aliases",)),
)
