from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.requests import REQUEST_TYPES
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Control


def parse_request(request: InteractionRequest, control: Control) -> InteractionRequest:
    """Validate the request record and pass it to branch selection.

    Raises:
        TypeError: The request has an unsupported internal/public record type.
    """
    if not isinstance(request, REQUEST_TYPES):
        raise TypeError("Unknown internal interaction request")
    return request


stage = Pass.define(
    name="parse_request",
    function=parse_request,
    contract=Contract(object, object),
)
