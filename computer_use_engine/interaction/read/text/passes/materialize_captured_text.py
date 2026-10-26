"""Materialize captured text."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.text.model import TextReadPlan
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control
    from computer_use.types import TextPage


def create(*, captured_text: Callable[..., TextPage]) -> Callable[[TextReadPlan, Control], TextPage]:

    def result(plan: TextReadPlan, control: Control) -> TextPage:
        """Convert the native text page into the public result using its captured source metadata."""
        return captured_text(cast("NativeTextPage", plan.page), plan.request.snapshot["source"])

    return result


stage = Pass.factory(
    name="materialize_captured_text",
    factory=create,
    contract=Contract(TextReadPlan, object, {Effect.RESOURCE}),
)
if TYPE_CHECKING:
    from computer_use.protocol.records import TextPage as NativeTextPage
