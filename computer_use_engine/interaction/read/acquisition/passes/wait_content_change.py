"""Wait content change."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import QUERY, ClassifiedAttempt
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[ClassifiedAttempt, Control], Awaitable[ClassifiedAttempt]]:
    async def content_wait(value: ClassifiedAttempt, control: Control) -> ClassifiedAttempt:
        """Wait for content-change evidence and attach a supported wait failure to the decision."""
        attempt = value.rechecked
        _, gap = await services._component(
            "wait",
            lambda: services.reader.wait_for_change(
                attempt.target, attempt.collected.components.tree, control.remaining
            ),
        )
        return replace(value, wait_gap=gap)

    return content_wait


stage = Pass.factory(
    name="wait_content_change",
    factory=create,
    contract=Contract(ClassifiedAttempt, ClassifiedAttempt, QUERY),
)
