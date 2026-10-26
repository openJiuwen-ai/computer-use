"""Select query kind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.requests import FindElement
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.requests import InteractionRequest


def execute(request: InteractionRequest, _: object) -> str:
    """Select path lookup for FindElement, otherwise application listing."""
    return "find" if isinstance(request, FindElement) else "applications"


stage = Pass.define(
    name="select_query_kind",
    function=execute,
    contract=Contract(object, str),
)
