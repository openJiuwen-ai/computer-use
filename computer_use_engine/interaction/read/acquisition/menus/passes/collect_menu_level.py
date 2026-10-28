"""Collect menu level."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import QUERY, MenuLevel, MenuLevelFacts
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


async def collect_menu_level(level: MenuLevel, control: Control) -> MenuLevelFacts:
    """Read the current parent's child menu identities and names within the menu scope."""
    query = level.request.query
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    siblings = await query._menu_children(level.current, level.ancestors)
    names = []
    for token in siblings:
        # pylint: disable-next=protected-access
        if await query._role(token) in ("window", "sheet"):
            query.fail("observation_incomplete", "A menu level crosses an independent window boundary.")
        # pylint: disable-next=protected-access
        names.append((token, await query._name(token)))
    return MenuLevelFacts(level, tuple(names))


stage = Pass.define(
    name="collect_menu_level",
    function=collect_menu_level,
    contract=Contract(MenuLevel, MenuLevelFacts, QUERY),
)
