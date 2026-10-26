"""Collect native facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.model import QUERY, CollectedFacts, FactProfile
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.pipeline.core import Control


def create(*, reader: NativeReader) -> Callable[[FactProfile, Control], Awaitable[CollectedFacts]]:
    async def collect(plan: FactProfile, control: Control) -> CollectedFacts:
        """Read the planned fields and graph scope under the active native lease and collection
        limits.
        """
        read, target = plan.scope.request.read, plan.scope.target
        reply = await reader.port.nodes(
            read.token,
            target,
            plan.fields,
            plan.scope.request.max_nodes,
            plan.max_depth,
            read.remaining(),
            scope_kind=plan.scope.request.scope_kind,
            **plan.options,
        )
        return CollectedFacts(plan, reply.observation, reply.metadata)

    return collect


stage = Pass.factory(
    name="collect_native_facts",
    factory=create,
    contract=Contract(FactProfile, CollectedFacts, QUERY),
)
