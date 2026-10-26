"""Record resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolvedWrite
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.pipeline.core import Control


def create(*, state: WriteRunState) -> Callable[[ResolvedWrite, Control], ResolvedWrite]:
    def record_resolution(resolved: ResolvedWrite, _: object) -> ResolvedWrite:
        """Retain the resolved menu, pointer, and ancestor evidence for the next attempt."""
        state.memory = resolved.memory
        return resolved

    return record_resolution


stage = Pass.factory(
    name="record_resolution",
    factory=create,
    contract=Contract(ResolvedWrite, ResolvedWrite, {Effect.RESOURCE}, writes=("resolution memory",)),
)
