"""Match menu title."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import (
    PURE,
    MatchedMenuItem,
    MenuLevelFacts,
)
from computer_use.interaction.read.presentation.formatting import jsonString
from computer_use.native.semantics import UNKNOWN
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def match_menu_title(value: MenuLevelFacts, control: Control) -> MatchedMenuItem:
    """Resolve one requested menu label from the observed sibling names.

    Require readable names and one exact match. Return the matched identity and ancestor
    chain, or report observed candidates when matching fails.
    """
    level, names = value.level, value.names
    query, index, title = level.request.query, level.index, level.title
    matched = [token for token, name in names if name == title]
    if any(name is UNKNOWN for _, name in names):
        query.fail("observation_incomplete", f"Menu level {index + 1} has unread item names; uniqueness is unknown.")
    if len(matched) != 1:
        titles = [name for _, name in names if isinstance(name, str) and name]
        candidates = ", ".join(jsonString(name) for name in titles[:30]) or "<none>"
        if len(titles) > 30:
            candidates += f"; {len(titles) - 30} more (read the parent menu)"
        query.fail(
            "ambiguous_target" if matched else "element_not_found",
            f"Menu level {index + 1}, title {jsonString(title)} has {len(matched)} matches. Candidates: {candidates}",
        )
    selected = matched[0]
    return MatchedMenuItem(level, selected, (*level.ancestors, selected))


stage = Pass.define(
    name="match_menu_title",
    function=match_menu_title,
    contract=Contract(MenuLevelFacts, MatchedMenuItem, PURE),
)
