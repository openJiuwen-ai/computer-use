"""Menu has working window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.menu.model import PURE, CollectedMenuObservation
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: CollectedMenuObservation, control: Control) -> bool:
    """Choose working-window rediscovery when the menu request contains a fixed selection."""
    return value.request.selection is not None


stage = Pass.define(
    name="menu_has_working_window",
    function=execute,
    contract=Contract(CollectedMenuObservation, bool, PURE),
)
