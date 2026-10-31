# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Filter route history policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import route_key
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.interaction.write.routes.selection.model import CandidateSet, EligibleRoutes
from computer_use.native.contracts import UnitCommand
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Any

    from computer_use.pipeline.core import Control


def filter_route_history_policy(value: Any) -> EligibleRoutes:
    """Exclude failed routes and candidates lacking required editing, enabled, or geometry evidence."""
    request = value.request
    facts, intent, attempts = request.facts, request.intent, request.history
    commands = []
    for primitive in value.primitives:
        if request.needs_editing_evidence and primitive.unit not in (
            "prepare_receiver",
            "native_text_edit",
            "native_value_replace",
        ):
            continue  # Readiness alone does not prove editing capability.
        command = UnitCommand(primitive, facts.binding, intent, facts.actuator, receiver=facts.receiver)
        if route_key(command) in attempts.unavailable:
            continue
        if not stage_admission(primitive).available:
            continue
        commands.append(command)
    return EligibleRoutes(request, value.primitives, tuple(commands))


def execute(value: CandidateSet, control: Control) -> EligibleRoutes:
    """Filter candidates by route history and required editing, enabled, and geometry evidence."""
    return filter_route_history_policy(value)


stage = Pass.define(
    name="filter_route_history_policy",
    function=execute,
    contract=Contract(CandidateSet, EligibleRoutes, requires=("read-only route history", "editing evidence")),
)
