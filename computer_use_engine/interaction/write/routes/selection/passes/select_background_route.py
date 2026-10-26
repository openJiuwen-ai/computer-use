"""Select background route."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import route_key
from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.native.contracts import Knowledge
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_background_route(options: RouteOptions) -> RouteOptions:
    """Select the first eligible background preparation route not yet attempted."""
    attempts = options.eligible.request.history
    for command, needs_foreground in options.preparation:
        primitive, key = command.primitive, route_key(command)
        failed = key in attempts.background_unavailable
        if (
            not needs_foreground
            and not failed
            and primitive.background_preparation != Knowledge.NO
            and (key, False) not in attempts.prepared
        ):
            return replace(options, selected=replace(command, prepare=True))
    return options


def execute(value: RouteOptions, control: Control) -> RouteOptions:
    """Choose an eligible background preparation command using the current attempt history."""
    return select_background_route(value)


stage = Pass.define(
    name="select_background_route",
    function=execute,
    contract=Contract(RouteOptions, RouteOptions),
)
