# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose native-unit preparation and delivery in one background-first policy."""

from dataclasses import replace

from computer_use.interaction.write.routes.policy import route_key
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.interaction.write.routes.selection.impact import least_disruptive_group
from computer_use.interaction.write.routes.selection.model import EligibleRoutes, RouteOptions
from computer_use.native.contracts import Channel
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(eligible: EligibleRoutes, control: Control) -> RouteOptions:
    """Select ready/addressed preparation before foreground and global mouse use.

    The operation's Attempts owner still records failures and preparation. This
    pure pass only consumes that history and never dispatches or retries input.
    """
    request = eligible.request
    facts, intent, attempts = request.facts, request.intent, request.history
    ready, background, properties, foreground = [], [], [], []
    ready_keyboard = any(
        command.primitive.unit == "key_sequence"
        and command.primitive.channel == Channel.KEYBOARD
        and stage_admission(command.primitive).ready
        for command in eligible.commands
    )
    for command in eligible.commands:
        primitive = command.primitive
        admission, key = stage_admission(primitive), route_key(command)
        if admission.ready:
            if primitive.unit == "native_value_replace" and not (
                intent.kind == "replace"
                and facts.platform == "macos"
                and facts.input.foreground_surface != facts.binding.surface
                and not ready_keyboard
            ):
                # Retain normal editing callbacks when the target has an editor
                # route. An independent background whole-value contract can run
                # directly without first building a keyboard responder chain.
                properties.append(command)
            else:
                ready.append(command)
            continue
        if (
            "background" in admission.preparation
            and key not in attempts.background_unavailable
            and (key, False) not in attempts.prepared
        ):
            background.append(replace(command, prepare=True))
        background_unavailable = "background" not in admission.preparation or key in attempts.background_unavailable
        if "foreground" in admission.preparation and background_unavailable and (key, True) not in attempts.prepared:
            foreground.append(replace(command, prepare=True, foreground=True))
    choices = least_disruptive_group(ready, background, properties, foreground)
    return RouteOptions(eligible, selected=choices[0] if choices else None)


stage = Pass.define(
    name="select_unit_route",
    function=execute,
    contract=Contract(EligibleRoutes, RouteOptions, requires=("route-local admission", "read-only attempts")),
)
