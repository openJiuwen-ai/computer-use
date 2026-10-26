"""Select read kind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.requests import ReadApp, ReadDesktop, ReadElement
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Control


def kind(request: InteractionRequest, control: Control) -> str:
    """Select the read branch matching the typed session request."""
    if isinstance(request, ReadApp):
        return "application"
    if isinstance(request, ReadDesktop):
        return "desktop"
    if isinstance(request, ReadElement):
        return "element"
    return "text"


stage = Pass.define(
    name="select_read_kind",
    function=kind,
    contract=Contract(object, str),
)
