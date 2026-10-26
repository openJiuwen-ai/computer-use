"""Collect captured text page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import (
    QUERY,
    CapturedTextPage,
    OpenCapturedTextRead,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, port: NativePort) -> Callable[[OpenCapturedTextRead, Control], Awaitable[CapturedTextPage]]:
    async def native_page(value: OpenCapturedTextRead, control: Control) -> CapturedTextPage:
        """Read the requested range from the immutable native text capture under the active lease."""
        plan, read = value.plan, value.read
        request = plan.request
        page = await port.captured_text(
            read.token, read.target, request.source.text_ref, request.offset, request.length, read.remaining()
        )
        return CapturedTextPage(plan, page)

    return native_page


stage = Pass.factory(
    name="collect_captured_text_page",
    factory=create,
    contract=Contract(OpenCapturedTextRead, CapturedTextPage, QUERY),
)
