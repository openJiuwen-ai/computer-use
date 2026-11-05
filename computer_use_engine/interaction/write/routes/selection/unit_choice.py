# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure eligibility and strategy shared by resolution and final unit choice."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.routes.policy import preparation_pair_available, route_key
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.interaction.write.routes.selection.impact import global_input_denied, least_disruptive_group
from computer_use.interaction.write.routes.selection.model import CandidateSet, EligibleRoutes, RouteOptions
from computer_use.native.contracts import Channel, UnitCommand

if TYPE_CHECKING:
    from computer_use.interaction.write.routes.selection.model import SelectionRequest
    from computer_use.native.contracts import Binding


def eligible_unit_routes(value: CandidateSet) -> EligibleRoutes:
    """Filter current route evidence without preparing, dispatching or consuming history."""
    request = value.request
    facts, intent, attempts = request.facts, request.intent, request.history
    commands = []
    for primitive in value.primitives:
        if request.needs_editing_evidence and primitive.unit not in (
            "prepare_receiver",
            "native_text_edit",
            "native_value_replace",
        ):
            continue
        command = UnitCommand(primitive, facts.binding, intent, facts.actuator, receiver=facts.receiver)
        if global_input_denied(command, request.policy, request.facts.platform):
            continue
        if route_key(command) in attempts.unavailable:
            continue
        admission = stage_admission(primitive)
        if not admission.available:
            continue
        if not admission.ready and not preparation_pair_available(command, attempts):
            continue
        commands.append(command)
    return EligibleRoutes(request, value.primitives, tuple(commands))


_DATETIME_LITERAL = re.compile(
    r"(?:[0-9]{1,4}([/.-])[0-9]{1,2}\1[0-9]{1,4}"
    r"(?: [0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?"
    r"|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)"
)


def _contextual_replacement(request: SelectionRequest) -> bool:
    """Retain the existing Windows editor preference without rejecting a setter."""
    facts = request.facts
    if facts.platform != "windows" or request.intent.kind != "replace" or cast("Binding", facts.binding).node is None:
        return False
    if facts.native_role == "ControlType.ComboBox":
        has_editor = facts.input.editor_owner == cast("Binding", facts.binding).node and facts.input.editor is not None
        return has_editor or any(p.unit == "key_sequence" and "selection" in p.pending for p in facts.primitives)
    return facts.native_role == "ControlType.Spinner" and bool(
        _DATETIME_LITERAL.fullmatch(request.intent.parameters.get("text", ""))
    )


def select_unit_route(eligible: EligibleRoutes) -> RouteOptions:
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
                request.needs_editing_evidence
                or intent.kind == "replace"
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
    groups = (ready, background, properties, foreground)
    # Prefer the contextual editing path only while it has an actual ready or
    # unconsumed preparation command. Candidate generation retains independent
    # setters, so a keyboard refusal before requested input cannot erase them.
    # The operation Gate alone decides whether a refusal permits another route.
    preferred = (
        least_disruptive_group(
            *(tuple(command for command in group if command.primitive.unit == "key_sequence") for group in groups)
        )
        if _contextual_replacement(request)
        else []
    )
    choices = preferred or least_disruptive_group(*groups)
    return RouteOptions(eligible, selected=choices[0] if choices else None)
