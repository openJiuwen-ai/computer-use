# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Refuse unavailable route."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import refuse
from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.native.contracts import Binding, Knowledge, UnitCommand
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def refuse_unavailable_route(options: RouteOptions) -> NoReturn:
    """Explain why the remaining native candidates cannot satisfy the requested input."""
    request = options.eligible.request
    facts, intent = request.facts, request.intent
    if request.needs_editing_evidence:
        refuse(
            "input_unavailable",
            facts,
            "The selected control has no confirmed text-editing capability or remaining preparation route.",
            "read the intended control or its window; focus alone does not authorize text input",
        )
    candidates = options.eligible.candidates
    if candidates and all("enabled" in p.pending for p in candidates):
        refuse(
            "input_unavailable",
            facts,
            "The selected control is disabled."
            if facts.enabled == Knowledge.NO
            else "The selected control has no confirmed enabled input route.",
            "read the control after it becomes enabled; foreground assistance cannot enable it",
        )
    if facts.unavailable_reason:
        refuse(
            "input_unavailable",
            facts,
            facts.unavailable_reason,
            "read the intended control and its current window image; choose a reachable control or image point",
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
            "return the current window or desktop image, then scroll at a visible point in the intended region",
        )
    refuse(
        "input_unavailable",
        facts,
        "No confirmed route can perform this intent on the original target.",
        "read the current target and its image to inspect the available input scope",
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
