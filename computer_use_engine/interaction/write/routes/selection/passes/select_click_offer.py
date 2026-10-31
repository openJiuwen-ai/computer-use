# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select one fixed delivery using route-local evidence and background-first policy."""

from dataclasses import replace

from computer_use.interaction.write.diagnostics import record_click_selection
from computer_use.interaction.write.routes.policy import refuse, route_key
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.interaction.write.routes.selection.impact import least_disruptive_group
from computer_use.interaction.write.routes.selection.model import CandidateSet
from computer_use.native.contracts import ClickSelectionDecision, ClickSelectionExclusion, UnitCommand
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(value: CandidateSet, control: Control) -> UnitCommand:
    """Prefer addressed delivery and preparation before occupying the global mouse.

    This is the only click selector. Native execution receives a single offer;
    preparation and gesture dispatch are performed in the same native boundary.
    """
    request = value.request
    facts, intent, history = request.facts, request.intent, request.history
    exclusions, ready, background, foreground = [], [], [], []
    selected_ids = {p.id for p in value.primitives}
    for primitive in facts.primitives:
        offer = primitive.click
        if offer is None:
            raise ValueError("A click inspection mixed plain and integrated contracts")
        command = UnitCommand(primitive, facts.binding, intent, facts.actuator, receiver=facts.receiver)
        admission = stage_admission(primitive)
        key = route_key(command)
        refresh = history.click_refresh
        # Foreground preparation may make the retained target ready. The same
        # family can consume its single refresh without reacquiring foreground;
        # this permission never upgrades a background attempt to activation.
        refreshed_ready = (
            offer.admission == "ready"
            and refresh is not None
            and refresh.activation is True
            and route_key(replace(command, foreground=True)) == refresh
        )
        reason = None
        if primitive.id not in selected_ids:
            reason = "semanticOrPreferenceExclusion"
        elif refresh is not None and key != refresh:
            if route_key(replace(command, foreground=True)) != refresh:
                reason = "continuationScope"
        if reason is None and not admission.available:
            reason = offer.admission + (": " + offer.reason if offer.reason else "")
        if reason is not None:
            exclusions.append(ClickSelectionExclusion(offer.route_family, reason, facts.observed))
            continue
        matches_refreshed_ready = (
            True
            if (key not in history.unavailable or key == refresh or refreshed_ready)
            and (refresh is None or key == refresh or refreshed_ready)
            else False
        )
        if matches_refreshed_ready:
            if admission.ready:
                ready.append(command)
            elif "background" in admission.preparation and key not in history.background_unavailable:
                background.append(command)
        foreground_command = replace(command, foreground=True)
        fgkey = route_key(foreground_command)
        matches_offer = (
            True
            if (
                "foreground" in admission.preparation
                and (fgkey not in history.unavailable or fgkey == refresh)
                and (history.click_refresh is None or fgkey == history.click_refresh)
            )
            else False
        )
        if matches_offer:
            foreground.append(foreground_command)
        reason = (
            "priorFailure"
            if key in history.unavailable or key in history.background_unavailable
            else "noBackgroundPreparation"
            if offer.admission != "ready" and "background" not in offer.preparation_options
            else "eligibleBackground"
        )
        exclusions.append(
            ClickSelectionExclusion(offer.route_family, reason, facts.observed, history.failed_steps.get(key))
        )
    # Activation and global pointer use are independent effects. A ready HID
    # click cannot preempt a valid addressed route just because it is ready.
    order = {p.id: index for index, p in enumerate(value.primitives)}
    choices = least_disruptive_group(ready, background, foreground)
    if not choices:
        record_click_selection(control, request, tuple(exclusions), None)
        detail = (
            facts.unavailable_reason or "No click delivery can satisfy the original target and requested semantics."
        )
        reasons = dict.fromkeys(
            p.click.reason
            for p in value.primitives
            if p.click.admission in ("unavailable", "insufficientEvidence") and p.click.reason
        )
        if reasons:
            detail += " " + " ".join(reason for reason in reasons if reason not in detail)
        refuse(
            "input_unavailable",
            facts,
            detail,
            "read the target and its current window image before choosing the next action",
        )
    command = min(choices, key=lambda c: order[c.primitive.id])
    decision = ClickSelectionDecision(
        command.primitive.click.route_family,
        command.foreground,
        tuple(item for item in exclusions if item.reason != "eligibleBackground"),
    )
    record_click_selection(control, request, decision.exclusions, command)
    return replace(command, click_selection=decision)


stage = Pass.define(
    name="select_click_offer",
    function=execute,
    contract=Contract(
        CandidateSet,
        UnitCommand,
        requires=("fixed intent", "route-local admission", "read-only attempts"),
        failures=("no permitted click route", "mixed click contracts"),
    ),
)
