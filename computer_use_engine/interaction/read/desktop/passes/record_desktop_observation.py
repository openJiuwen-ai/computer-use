"""Record desktop observation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.requests import ReadDesktop
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record
    from computer_use.session.desktop_store import DesktopStore
    from computer_use.types import Observation


def create(
    *, desktop: DesktopStore, observation_state: Callable[[Record], Observation]
) -> Callable[[tuple[ReadDesktop, Record], Control], tuple[ReadDesktop, Record, Observation]]:

    def record(value: tuple[ReadDesktop, Record], control: Control) -> tuple[ReadDesktop, Record, Observation]:
        """Retain desktop coordinate metadata and create public state for the captured image and
        text.
        """
        request, observed = value
        desktop.record(observed)
        from computer_use.session.observation import lazyObservation

        state = lazyObservation(
            observed["document"], observed["screenshot"], details_document=observed["document"], overview=None
        )
        return (request, observed, state)

    return record


stage = Pass.factory(
    name="record_desktop_observation",
    factory=create,
    contract=Contract(tuple, tuple, {Effect.RESOURCE}),
)
