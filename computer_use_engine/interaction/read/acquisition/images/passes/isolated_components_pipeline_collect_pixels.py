"""Isolated components pipeline collect pixels."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.components.model import QUERY, ComponentLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[ComponentLease, Control], Awaitable[WindowImage]]:
    async def image_read(value: ComponentLease, control: Control) -> WindowImage:
        """Capture window pixels using half the remaining budget in the isolated image lease."""
        return await services.images.collect(
            cast("OperationLease[Binding]", value.read).bounded(
                max(1, cast("Callable[[], int]", control.remaining)() // 2)
            ),
            max_dimension=value.request.limits["max_dimension"],
        )

    return image_read


stage = Pass.factory(
    name="collect_pixels",
    factory=create,
    contract=Contract(ComponentLease, object, QUERY),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
