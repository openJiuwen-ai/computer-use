from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import PURE, MenuItemFacts
from computer_use.interaction.read.presentation.formatting import json_string
from computer_use.pipeline import Contract, Pass

"Select submenu."


if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_submenu(value: MenuItemFacts, control: Control) -> tuple[str, tuple[str, ...]]:
    """Resolve the submenu container for the matched path component.

    Use a menu item directly when it is a menu, or unwrap its single exposed submenu
    container. For a remaining path component, report a missing or ambiguous submenu from
    the collected child-role evidence.
    """
    matched, role = value.matched, value.role
    level, current, ancestors = matched.level, matched.selected, matched.ancestors
    if role == "menu":
        # GTK's named item is the submenu, not an anonymous wrapper.
        return current, ancestors
    wrappers = [token for token, child_role in value.roles if child_role == "menu"]
    if role in ("menuItem", "menuBarItem") and len(value.children) == 1 and len(wrappers) == 1:
        current = wrappers[0]
        ancestors = (*ancestors, current)
    elif level.index != len(level.request.path) - 1:
        if not value.children:
            level.request.query.fail(
                "element_not_found", f"{json_string(level.title)} has no submenu; a command must end the path."
            )
        level.request.query.fail(
            "ambiguous_target" if len(wrappers) > 1 else "observation_incomplete",
            (
                f"{json_string(level.title)} does not expose one unambiguous native submenu container; read "
                f"this item first."
            ),
        )
    return current, ancestors


stage = Pass.define(
    name="select_submenu",
    function=select_submenu,
    contract=Contract(MenuItemFacts, tuple, PURE),
)
