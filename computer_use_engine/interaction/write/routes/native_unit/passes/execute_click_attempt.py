"""Send one integrated native click attempt within the existing invocation lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import AttemptOutcome, NativeInvocation
from computer_use.interaction.write.routes.native_unit.dispatch import send_native_unit
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort


def create(
    *, native: NativePort, state: WriteRunState
) -> Callable[[NativeInvocation, object], Awaitable[AttemptOutcome]]:
    async def execute(invocation: NativeInvocation, control: object) -> AttemptOutcome:
        """Prepare, resolve the final point, dispatch, and clean up in one native call."""
        if invocation.unit.command.prepare:
            raise ValueError("An integrated click cannot issue a separate preparation command")
        return await send_native_unit(invocation, native, state)

    return execute


stage = Pass.factory(
    name="execute_click_attempt",
    factory=create,
    contract=Contract(NativeInvocation, AttemptOutcome, {Effect.PREPARE, Effect.DISPATCH, Effect.RESOURCE}),
)
