from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.components.model import ComponentLease
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.protocol.captures import capture_metadata

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from typing import AsyncIterator, Callable

    from computer_use.interaction.read.acquisition.components.model import ComponentRequest
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def image_notes(image: WindowImage | None) -> tuple[ObservationGap, ...]:
    """Convert screenshot stop reasons into image observation gaps.

    Args:
        image: Captured pixels and their original coordinate/identity metadata, when available.
    """
    if image is None:
        return ()
    return tuple(
        ObservationGap(
            "image",
            "observation_incomplete",
            reason,
            "use clearly visible content; read again if required content is missing",
        )
        for reason in capture_metadata(image.image).get("stopReasons", ())
    )


def native_lease(
    services: NativeAppReader,
) -> Callable[[ComponentRequest, Control], AbstractAsyncContextManager[ComponentLease]]:
    """Open the component request's native operation under the original call budget.

    Args:
        services: Existing read services and their narrow native/reference dependencies.
    """

    @asynccontextmanager
    async def factory(request: ComponentRequest, control: Control) -> AsyncIterator[ComponentLease]:
        target = replace(request.target, node=None, capture=None)
        async with services.port.calls.operation(target, cast("Callable[[], int]", control.remaining)) as read:
            yield ComponentLease(request, read)

    return factory
