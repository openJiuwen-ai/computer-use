from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.loading import window_content_unexposed
from computer_use.interaction.read.acquisition.model import AcquiredObservation
from computer_use.interaction.read.numbering.observation.model import PURE
from computer_use.pipeline import Contract, Pass

"Account acquisition gaps."


if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[AcquiredObservation, Control], AcquiredObservation]:
    def add_read_gaps(acquired: AcquiredObservation, control: Control) -> AcquiredObservation:
        "Append directory-coverage and unexposed-content evidence to the acquisition's gaps."
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        gaps = acquired.gaps + tuple(services._coverage(acquired.directory))
        limits, tree = acquired.plan.limits, acquired.components.tree
        if limits["fields"] is None and limits["max_depth"] is None and window_content_unexposed(tree):
            gaps += (
                ObservationGap(
                    "content",
                    "observation_incomplete",
                    "No identifiable content controls or text were exposed in this bounded window observation. "
                    "This does not establish that the window is empty or ready; "
                    "content may be loading, suspended or inaccessible.",
                    ("inspect the window screenshot; read again after an explicit user-visible operation if needed"),
                ),
            )
        return replace(acquired, gaps=gaps)

    return add_read_gaps


stage = Pass.factory(
    name="account_acquisition_gaps",
    factory=create,
    contract=Contract(AcquiredObservation, AcquiredObservation, PURE),
)
