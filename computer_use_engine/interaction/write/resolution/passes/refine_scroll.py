"""Refine scroll."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.resolution.scroll import ScrollPointerQuery
from computer_use.interaction.write.routes.policy import needs_pointer_member, validate_target
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def refine_scroll(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Refine an element-scroll route using the current wheel hit.

        When pointer geometry is needed, qualify the hit inside the selected scrolling
        region. Retain the member and return freshly inspected facts with its actuator
        and supporting evidence.
        """
        intent, target, operation, remaining = parts(frame)
        facts = cast("Facts", frame.facts)
        matches_needs_pointer_member = (
            True
            if (
                intent.kind == "scroll"
                and facts.actuator is not None
                and (facts.actuator.relation == "scrollPointer")
                and (facts.input.pointer_hit not in (None, facts.actuator.node))
                and needs_pointer_member(facts, intent, frame.request.history)
            )
            else False
        )
        if matches_needs_pointer_member:
            query = ScrollPointerQuery(native, operation, target, remaining)
            selected = await query.resolve(facts.input.pointer_hit)
            if selected is not None:
                memory = replace(frame.request.memory, scroll_member=selected.node)
                facts = await native.inspect(
                    operation, target, remaining(), within=memory.within, actuator=selected, basis=query.basis
                )
                if not facts.binding.same_target(target):
                    raise ValueError("Native scroll inspection changed the requested element")
                validate_target(facts, native.policy)
                return replace(
                    frame,
                    facts=facts,
                    scope={"within": memory.within, "actuator": selected, "basis": query.basis},
                    request=replace(frame.request, memory=memory),
                )
        return frame

    return refine_scroll


stage = Pass.factory(
    name="ScrollPointerRefinementPipeline",
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
