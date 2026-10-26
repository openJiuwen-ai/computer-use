"""Dispatch native unit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import AttemptOutcome, NativeInvocation
from computer_use.interaction.write.routes.native_unit.dispatch import send_native_unit
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(
    *, native: NativePort, state: WriteRunState
) -> Callable[[NativeInvocation, Control], Awaitable[AttemptOutcome]]:
    async def dispatch_native_unit(invocation: NativeInvocation, control: Control) -> AttemptOutcome:
        """Send the selected input unit with the shared invocation budget."""
        return await send_native_unit(invocation, native, state)

    return dispatch_native_unit


stage = Pass.factory(
    name="dispatch_native_unit",
    factory=create,
    contract=Contract(NativeInvocation, AttemptOutcome, {Effect.DISPATCH}),
)
