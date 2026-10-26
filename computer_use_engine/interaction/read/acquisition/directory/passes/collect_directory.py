"""Collect directory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.directory.model import DirectoryPlan
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


def create(*, port: NativePort) -> Callable[[DirectoryPlan, Control], Awaitable[DirectoryFacts]]:
    async def collect(plan: DirectoryPlan, control: Control) -> DirectoryFacts:
        """Read the requested native catalogue and check the enclosing operation's deadline."""
        result = await port.directory(plan.read.token, plan.read.target, plan.query, plan.read.remaining())
        plan.read.remaining()
        return result

    return collect


stage = Pass.factory(
    name="collect_directory",
    factory=create,
    contract=Contract(DirectoryPlan, dict, {Effect.QUERY}),
)
