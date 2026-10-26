from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.publication.model import PublishedObservation
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record
    from computer_use.types import Observation


def create(*, publish_observation: Callable[[Record], None]) -> Callable[[PublishedObservation, Control], Observation]:
    def publish(value: PublishedObservation, control: Control) -> Observation:
        """Publish the prepared observation event and return its public result."""
        publish_observation(value.event)
        return value.result

    return publish


stage = Pass.factory(
    name="publish_observation_event",
    factory=create,
    contract=Contract(PublishedObservation, object, {Effect.RESOURCE}),
)
