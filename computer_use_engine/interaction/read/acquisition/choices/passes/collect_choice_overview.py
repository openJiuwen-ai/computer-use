"""Collect choice overview."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.choices.model import (
    WindowChoiceDraft,
    WindowChoiceRequest,
)
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[WindowChoiceRequest, Control], Awaitable[WindowChoiceDraft]]:
    async def overview(request: WindowChoiceRequest, control: Control) -> WindowChoiceDraft:
        """Collect requested candidate-window previews, or retain a draft with no overview."""
        if not request.include_overview:
            return WindowChoiceDraft(request, None)
        value = await services.overviews.collect(
            request.directory,
            services.window_aliases(request.directory),
            cast("Callable[[], int]", control.remaining),
            root=request.selection.root.root_window if request.selection else None,
            include_single=True,
        )
        return WindowChoiceDraft(request, value)

    return overview


stage = Pass.factory(
    name="collect_choice_overview",
    factory=create,
    contract=Contract(WindowChoiceRequest, WindowChoiceDraft, {Effect.QUERY, Effect.RESOURCE}),
)
