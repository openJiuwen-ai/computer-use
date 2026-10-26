"""Check menu delivery budget."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.delivery.menu.model import QUERY, MenuTextDraft
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def check_budget(value: MenuTextDraft, control: Control) -> MenuTextDraft:
    """Check the enclosing call deadline before returning the menu draft."""
    cast("Callable[[], int]", control.remaining)()
    return value


stage = Pass.define(
    name="check_menu_delivery_budget",
    function=check_budget,
    contract=Contract(MenuTextDraft, MenuTextDraft, QUERY),
)
if TYPE_CHECKING:
    from typing import Callable
