from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.agent.requests import QUERY_REQUESTS, WRITE_REQUESTS
from computer_use.interaction.requests import ExecuteAction, FindElement, ListApplications
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Control


def select_request_kind(request: InteractionRequest, control: Control) -> str:
    """Classify a supported request as read, write, or query."""
    if isinstance(request, WRITE_REQUESTS):
        return "write"
    if isinstance(request, QUERY_REQUESTS):
        return "query"
    if isinstance(request, ExecuteAction):
        return "write"
    if isinstance(request, (ListApplications, FindElement)):
        return "query"
    return "read"


stage = Pass.define(
    name="select_request_kind",
    function=select_request_kind,
    contract=Contract(object, str),
)
