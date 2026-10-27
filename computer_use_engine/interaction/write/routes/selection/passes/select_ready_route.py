"""Select ready route."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import route_key
from computer_use.interaction.write.routes.selection.model import EligibleRoutes, RouteOptions
from computer_use.native.contracts import Channel, Knowledge, UnitCommand
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_ready_route(eligible: EligibleRoutes) -> RouteOptions:
    """Choose a ready unit and retain candidates needing preparation or property replacement.

    For contextual text edits, a preparable keyboard route may take priority
    so that input follows the control's normal editing callbacks.
    """
    request = eligible.request
    facts, intent, attempts = request.facts, request.intent, request.history
    preparation, property_replacements = [], []
    for command in eligible.commands:
        primitive, key = command.primitive, route_key(command)
        if primitive.readiness == Knowledge.YES:
            if primitive.unit == "native_value_replace":
                # Whole-value replacement is already an addressed-object
                # contract. In shared macOS background use it before preparing
                # process keys, whose delivery does not prove consumption.
                # An already-ready keyboard retains normal edit callbacks.
                # Receiver resolution has already excluded a separate editor;
                # this does not make a value setter an insertion/commit route.
                background_replacement = (
                    intent.kind == "replace"
                    and facts.platform == "macos"
                    and request.policy is not None
                    and request.policy.mode == "shared"
                    and facts.input.foreground_surface != facts.binding.surface
                    and not any(
                        item.primitive.unit == "key_sequence"
                        and item.primitive.channel == Channel.KEYBOARD
                        and item.primitive.readiness == Knowledge.YES
                        for item in eligible.commands
                    )
                )
                if not background_replacement:
                    property_replacements.append(command)
                    continue
            if primitive.unit == "prepare_receiver" and (key, False) in attempts.prepared:
                continue
            # Ready contextual setters retain the existing bounded preference
            # for a preparable keyboard route before any text mutation.
            matches_intent = (
                True
                if (
                    intent.kind in ("insert", "replace")
                    and not request.needs_editing_evidence
                    and primitive.unit == "native_text_edit"
                    and primitive.channel == Channel.EDITOR
                )
                else False
            )
            if matches_intent:
                for keyboard in eligible.candidates:
                    matches_keyboard = (
                        True
                        if (
                            keyboard.unit != "key_sequence"
                            or keyboard.channel != Channel.KEYBOARD
                            or keyboard.pending != ("receiver",)
                            or keyboard.background_preparation != Knowledge.YES
                        )
                        else False
                    )
                    if matches_keyboard:
                        continue
                    prepared_command = UnitCommand(
                        keyboard, facts.binding, intent, facts.actuator, receiver=facts.receiver
                    )
                    prepared_key = route_key(prepared_command)
                    if (
                        prepared_key not in attempts.unavailable
                        and prepared_key not in attempts.background_unavailable
                        and (prepared_key, False) not in attempts.prepared
                    ):
                        return RouteOptions(eligible, selected=replace(prepared_command, prepare=True))
            return RouteOptions(eligible, selected=replace(command, prepare=primitive.unit == "prepare_receiver"))
        preparation.append((command, "foreground" in primitive.pending))
    return RouteOptions(eligible, tuple(preparation), tuple(property_replacements))


def execute(value: EligibleRoutes, control: Control) -> RouteOptions:
    """Select ready input or retain the preparation choices it needs.

    Prefer the eligible ready unit, accounting for contextual text-editing
    callbacks. Otherwise return the commands needing preparation and the ready
    property replacements for the following route-selection branches.
    """
    return select_ready_route(value)


stage = Pass.define(
    name="select_ready_route",
    function=execute,
    contract=Contract(EligibleRoutes, RouteOptions),
)
