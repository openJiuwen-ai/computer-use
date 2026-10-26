"""Snapshot resolution input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import BoundWrite, ResolutionInput
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.pipeline.core import Control


def create(*, state: WriteRunState) -> Callable[[BoundWrite, Control], ResolutionInput]:
    def resolution_input(bound: BoundWrite, _: object) -> ResolutionInput:
        """Capture the current route history and retained evidence for one resolution attempt."""
        return ResolutionInput(bound, state.view().history, state.memory)

    return resolution_input


stage = Pass.factory(
    name="snapshot_resolution_input",
    factory=create,
    contract=Contract(BoundWrite, ResolutionInput),
)
