"""Resolve scroll."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.resolution.scroll import PageRouteUnavailable, ScrollPointerQuery, ScrollQuery
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def resolve_scroll(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Resolve element scrolling to a page button or wheel region.

        Try the native page route while it remains eligible, then use a supported
        wheel route when needed. Attach the selected actuator and its fact dependencies
        to the inspection scope.
        """
        intent, target, operation, remaining = parts(frame)
        capabilities = getattr(native, "capabilities", ())
        selected = None
        if "nativeScroll" in capabilities and (
            not any((route.unit == "native_scroll" for route in frame.request.history.unavailable))
        ):
            query: ScrollQuery | ScrollPointerQuery = ScrollQuery(native, operation, target, remaining)
            try:
                selected = await query.resolve(intent.parameters)
            except PageRouteUnavailable:
                if "nativeElementScroll" not in capabilities:
                    raise
        if selected is None and "nativeElementScroll" in capabilities:
            query = ScrollPointerQuery(native, operation, target, remaining)
            selected = await query.resolve(frame.request.memory.scroll_member)
        return (
            replace(frame, scope={**frame.scope, "actuator": selected, "basis": query.basis})
            if selected is not None
            else frame
        )

    return resolve_scroll


stage = Pass.factory(
    name="ScrollTargetPipeline",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
