"""Route one session request to its execution branch in a single decision.

The branch key encodes both axes that the router needs: who issued the request
(public handle or the session itself) and its effect class (read, write, query).
Internal requests exist only for write and element lookup; all reads enter
through the handle branch.
Handle requests share one ``agent`` branch; the key keeps their effect class so
the trace still labels every call as read, write, or query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.agent.requests import QUERY_REQUESTS, READ_REQUESTS, WRITE_REQUESTS
from computer_use.interaction.requests import ExecuteAction, FindElement
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Control

AGENT_READ, AGENT_WRITE, AGENT_QUERY = "agent:read", "agent:write", "agent:query"
WRITE, QUERY = "write", "query"
AGENT_ROUTES = (AGENT_READ, AGENT_WRITE, AGENT_QUERY)

_ROUTES: tuple[tuple[tuple[type, ...], str], ...] = (
    (READ_REQUESTS, AGENT_READ),
    (WRITE_REQUESTS, AGENT_WRITE),
    (QUERY_REQUESTS, AGENT_QUERY),
    ((ExecuteAction,), WRITE),
    ((FindElement,), QUERY),
)


def select_route(request: InteractionRequest, control: Control) -> str:
    """Validate the request record and name the branch that executes it.

    Raises:
        TypeError: The request has an unsupported internal/public record type.
    """
    for types, route in _ROUTES:
        if isinstance(request, types):
            return route
    raise TypeError("Unknown internal interaction request")


stage = Pass.define(
    name="select_route",
    function=select_route,
    contract=Contract(object, str),
)
