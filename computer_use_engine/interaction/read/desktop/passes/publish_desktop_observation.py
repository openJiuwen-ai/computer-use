"""Publish desktop observation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.protocol.primitives import frozen

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.requests import ReadDesktop
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record
    from computer_use.types import Observation


def create(
    *, publish_observation: Callable[[Record], None]
) -> Callable[[tuple[ReadDesktop, Record, Observation], Control], Observation]:

    def publish(value: tuple[ReadDesktop, Record, Observation], control: Control) -> Observation:
        """Publish the desktop state with its native capture provenance and requested full flag."""
        request, observed, state = value
        publish_observation(
            dict(
                scope=observed["scope"],
                observed=observed["observed"],
                view="desktop",
                state=state,
                document=observed["document"],
                refs=frozen({}),
                full=request.full,
            )
        )
        return state

    return publish


stage = Pass.factory(
    name="publish_desktop_observation",
    factory=create,
    contract=Contract(tuple, object, {Effect.RESOURCE}),
)
