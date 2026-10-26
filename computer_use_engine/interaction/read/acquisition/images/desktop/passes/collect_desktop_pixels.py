"""Collect desktop pixels."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.images.desktop.model import DesktopLease
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, port: NativePort) -> Callable[[DesktopLease, Control], Awaitable[DesktopLease]]:
    async def capture(value: DesktopLease, control: Control) -> DesktopLease:
        """Capture desktop pixels at the requested size and attach the result after checking the
        deadline.
        """
        result = await port.capture(
            value.read.token, value.read.target, value.request.max_dimension, value.read.remaining(), kind="desktop"
        )
        value.read.remaining()
        return replace(value, result=result)

    return capture


stage = Pass.factory(
    name="collect_desktop_pixels",
    factory=create,
    contract=Contract(DesktopLease, DesktopLease, {Effect.QUERY}),
)
