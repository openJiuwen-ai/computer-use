"""Open frame."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame, ResolutionInput
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    pass


def open_frame(request: ResolutionInput, _: object) -> ResolutionFrame:
    """Start a resolution frame with the retained ancestor constraints."""
    within = request.memory.within
    return ResolutionFrame(request, {"within": within} if within else {})


stage = Pass.define(
    name="open_resolution",
    function=open_frame,
    contract=Contract(ResolutionInput, ResolutionFrame),
)
