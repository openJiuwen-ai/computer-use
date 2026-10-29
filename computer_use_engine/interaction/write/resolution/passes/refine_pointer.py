# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Refine pointer."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.resolution.control_part import ControlPartQuery
from computer_use.interaction.write.routes.policy import needs_pointer_member, validate_target
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def refine_pointer(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Refine a click route using the native pointer hit.

        When remaining click candidates require geometry, qualify the hit as a member
        of the addressed control. Retain the confirmed member and reinspect the target
        with its actuator and supporting evidence.
        """
        intent, target, operation, remaining = parts(frame)
        facts = cast("Facts", frame.facts)
        matches_needs_pointer_member = (
            True
            if (
                intent.kind == "click"
                and "nativeControlParts" in getattr(native, "capabilities", ())
                and (cast("Binding", target).node is not None)
                and (cast("Binding", target).capture is None)
                and (facts.input.pointer_hit not in (None, cast("Binding", target).node))
                and needs_pointer_member(facts, intent, frame.request.history)
            )
            else False
        )
        if matches_needs_pointer_member:
            query = ControlPartQuery(native, operation, target, remaining, intent=intent)
            selected = await query.resolve(facts.input.pointer_hit)
            if selected is not None:
                memory = replace(frame.request.memory, pointer_member=selected.node)
                facts = await native.inspect(
                    operation, target, remaining(), within=memory.within, actuator=selected, basis=query.basis
                )
                if not facts.binding.same_target(target):
                    raise ValueError("Native control-part inspection changed the requested owner")
                validate_target(facts, native.policy)
                return replace(
                    frame,
                    facts=facts,
                    scope={"within": memory.within, "actuator": selected, "basis": query.basis},
                    request=replace(frame.request, memory=memory),
                )
        return frame

    return refine_pointer


stage = Pass.factory(
    name="ControlPartRefinementPipeline",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import Facts
if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
