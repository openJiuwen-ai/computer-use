"""Ensure facts."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.routes.policy import validate_target
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def ensure_facts(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Populate and validate the target's native input facts.

        Inspect the target when the selected resolver left facts pending. Validate its
        identity and input bounds, and return the frame with those facts attached.
        """
        _, target, operation, remaining = parts(frame)
        facts = frame.facts
        if facts is None:
            facts = await native.inspect(operation, target, remaining(), **frame.scope)
        if not facts.binding.same_target(target):
            raise ComputerUseError(
                dict(
                    code="stale_target",
                    effect="none",
                    target=facts.target_label,
                    detail="Native inspection returned a different target.",
                    next="read the intended window again",
                )
            )
        validate_target(facts, native.policy)
        return replace(frame, facts=facts)

    return ensure_facts


stage = Pass.factory(
    name="inspect_target",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
