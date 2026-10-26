"""Image-only desktop observation has no accessibility-tree prerequisite."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.images.desktop.model import DesktopLease
from computer_use.interaction.read.acquisition.images.desktop.passes.collect_desktop_pixels import (
    stage as collect_desktop_pixels,
)
from computer_use.interaction.read.acquisition.images.desktop.passes.deliver_desktop_observation import (
    stage as deliver_desktop_observation,
)
from computer_use.interaction.read.acquisition.images.desktop.passes.format_desktop_observation import (
    stage as format_desktop_observation,
)
from computer_use.interaction.read.acquisition.images.desktop.passes.parse_desktop_request import (
    stage as parse_desktop_request,
)
from computer_use.interaction.read.acquisition.images.desktop.passes.validate_desktop_scope import (
    stage as validate_desktop_scope,
)
from computer_use.pipeline import Pipeline, Scope, Sequence

if TYPE_CHECKING:
    from typing import AsyncIterator

    from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record


class DesktopReadPipeline(Pipeline["DesktopReadRequest", "Record"]):
    """Collect a desktop image and its observation metadata.

    Validate image limits, open the session-level capture scope, and collect native desktop
    pixels. Format the desktop layout and image notes before returning the capture payload.

    Attributes:
        port: Native port and lifecycle service used for desktop capture.
    """

    def __init__(self, port: NativePort) -> None:
        self.port = port
        super().__init__(root=self.build())

    def build(self) -> Step[DesktopReadRequest, Record]:
        port = self.port

        @asynccontextmanager
        async def lease(request: DesktopReadRequest, control: Control) -> AsyncIterator[DesktopLease]:
            async with port.calls.open(None, request.timeout_ms) as read:
                yield DesktopLease(request, read)

        return Sequence(
            name="DesktopReadPipeline",
            steps=[
                parse_desktop_request(),
                Scope(
                    name="DesktopReadScope",
                    using=lease,
                    body=NativeScopeBody(
                        port,
                        Sequence(
                            name="DesktopImageObservation",
                            steps=[
                                validate_desktop_scope(),
                                collect_desktop_pixels(port=port),
                                format_desktop_observation(),
                                deliver_desktop_observation(),
                            ],
                        ),
                        lambda value: value.read.remaining,
                    ),
                ),
            ],
        )
