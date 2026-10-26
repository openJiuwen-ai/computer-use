"""Identify requests that require public App/Window/Element translation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.agent.requests import INTERFACE_REQUESTS
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Control


def is_agent_interface(request: InteractionRequest, control: Control) -> bool:
    """Identify public UI-handle requests that enter the agent adapter."""
    return isinstance(request, INTERFACE_REQUESTS)


stage = Pass.define(
    name="is_agent_interface",
    function=is_agent_interface,
    contract=Contract(object, bool),
)
