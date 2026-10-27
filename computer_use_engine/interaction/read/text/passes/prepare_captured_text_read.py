"""Prepare captured text read."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.read.text.model import TextReadPlan
from computer_use.interaction.requests import ReadText
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control


def create(*, validate_snapshot: Callable[[Binding], None]) -> Callable[[ReadText, Control], TextReadPlan]:

    def prepare(request: ReadText, control: Control) -> TextReadPlan:
        """Validate the selected snapshot and resolve its retained complete text source."""
        validate_snapshot(request.snapshot["binding"])
        source = request.snapshot.get("text")
        if source is None:
            raise local_failure(
                "this element has no complete captured text value",
                "read the element and inspect its available text fields",
                "observation_incomplete",
            )
        return TextReadPlan(request, source)

    return prepare


stage = Pass.factory(
    name="prepare_captured_text_read",
    factory=create,
    contract=Contract(ReadText, TextReadPlan),
)
