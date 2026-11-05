# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Refuse unavailable route."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import preparation_pair_available, refuse
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.interaction.write.routes.selection.impact import global_input_denied, global_input_failure
from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.native.contracts import Binding, Knowledge, UnitCommand
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def refuse_unavailable_route(options: RouteOptions) -> NoReturn:
    """Explain why the remaining native candidates cannot satisfy the requested input."""
    request = options.eligible.request
    facts, intent = request.facts, request.intent
    candidates = options.eligible.candidates
    if any(
        stage_admission(primitive).available
        and global_input_denied(
            UnitCommand(primitive, facts.binding, intent, facts.actuator, receiver=facts.receiver),
            request.policy,
            facts.platform,
        )
        for primitive in candidates
    ):
        code, detail = global_input_failure(request.policy, facts.platform)
        refuse(code, facts, detail)
    if any(
        stage_admission(primitive).preparation
        and not preparation_pair_available(
            UnitCommand(primitive, facts.binding, intent, facts.actuator, receiver=facts.receiver), request.history
        )
        for primitive in candidates
    ):
        refuse(
            "input_unavailable",
            facts,
            "The selected receiver still needs preparation after the receiver preparation allowance was consumed.",
        )
    if request.needs_editing_evidence:
        refuse(
            "input_unavailable",
            facts,
            "The selected control has no confirmed text-editing capability or remaining preparation route.",
        )
    if candidates and all("enabled" in p.pending for p in candidates):
        refuse(
            "input_unavailable",
            facts,
            "The selected control is disabled."
            if facts.enabled == Knowledge.NO
            else "The selected control has no confirmed enabled input route.",
        )
    if facts.unavailable_reason:
        refuse(
            "input_unavailable",
            facts,
            facts.unavailable_reason,
        )
    matches_binding = (
        True
        if (
            facts.platform == "windows"
            and intent.kind == "scroll"
            and isinstance(facts.binding, Binding)
            and facts.binding.node is not None
            and not any(p.unit == "native_scroll" and p.supported == Knowledge.YES for p in facts.primitives)
        )
        else False
    )
    if matches_binding:
        refuse(
            "input_unavailable",
            facts,
            "No usable scrolling action could be confirmed on the selected element.",
        )
    refuse(
        "input_unavailable",
        facts,
        "No confirmed route can perform this intent on the original target.",
    )


def execute(value: RouteOptions, control: Control) -> UnitCommand:
    """Raise an input-unavailable error explaining the target's remaining route constraints."""
    return refuse_unavailable_route(value)


stage = Pass.define(
    name="refuse_unavailable_route",
    function=execute,
    contract=Contract(RouteOptions, UnitCommand, failures=("no usable route",)),
)
if TYPE_CHECKING:
    from typing import NoReturn
