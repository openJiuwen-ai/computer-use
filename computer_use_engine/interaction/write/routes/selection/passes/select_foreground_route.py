"""Select foreground route."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import route_key
from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.native.contracts import Knowledge
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_foreground_route(options: RouteOptions) -> RouteOptions:
    """Select authorized foreground preparation after background preparation is unavailable."""
    request = options.eligible.request
    attempts, policy = request.history, request.policy
    for command, needs_foreground in options.preparation:
        primitive, key = command.primitive, route_key(command)
        background_unavailable = (
            needs_foreground
            or primitive.background_preparation == Knowledge.NO
            or key in attempts.background_unavailable
        )
        if background_unavailable and policy.foreground_allowed and (key, True) not in attempts.prepared:
            return replace(options, selected=replace(command, prepare=True, foreground=True))
    return options


def execute(value: RouteOptions, control: Control) -> RouteOptions:
    """Choose authorized foreground preparation after the background route is unavailable."""
    return select_foreground_route(value)


stage = Pass.define(
    name="select_foreground_route",
    function=execute,
    contract=Contract(RouteOptions, RouteOptions),
)
