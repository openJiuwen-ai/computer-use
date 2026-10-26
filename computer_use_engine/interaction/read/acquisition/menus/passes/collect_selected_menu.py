"""Collect selected menu."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import (
    QUERY,
    MatchedMenuItem,
    MenuItemFacts,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


async def collect_selected_menu(matched: MatchedMenuItem, control: Control) -> MenuItemFacts:
    """Read the matched item's role and child-role evidence needed to resolve its submenu."""
    query = matched.level.request.query
    role = await query._role(matched.selected)
    if role == "menu":
        return MenuItemFacts(matched, role)
    children = await query._menu_children(matched.selected, matched.ancestors)
    roles = tuple([(token, await query._role(token)) for token in children])
    return MenuItemFacts(matched, role, tuple(children), roles)


stage = Pass.define(
    name="collect_selected_menu",
    function=collect_selected_menu,
    contract=Contract(MatchedMenuItem, MenuItemFacts, QUERY),
)
