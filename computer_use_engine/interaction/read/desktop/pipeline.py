"""Desktop capture and publication retain their original failure boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.operation import HostOperation
from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
from computer_use.interaction.read.acquisition.images.desktop.pipeline import DesktopReadPipeline
from computer_use.interaction.read.desktop.passes.publish_desktop_observation import (
    stage as publish_desktop_observation,
)
from computer_use.interaction.read.desktop.passes.record_desktop_observation import (
    stage as record_desktop_observation,
)
from computer_use.pipeline import Contract, Sequence
from computer_use.pipeline.core import Node
from computer_use.session.events import pending_observations_discarded

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import ReadDesktop
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record
    from computer_use.types import Observation


class CollectDesktop(Node["ReadDesktop", "tuple[ReadDesktop, Record]"]):
    """Capture desktop evidence for a session read request.

    Run native desktop collection and pair its capture payload with the original read
    request. The following stages retain coordinate metadata and publish the observation.

    Attributes:
        capture: Bounded desktop capture flow using the controller's native port.
    """

    def __init__(self, controller: SessionController) -> None:
        self.capture = DesktopReadPipeline(controller.port)
        self.children = (self.capture,)
        super().__init__("CollectDesktop", Contract(object, tuple, self.capture.contract.effects))

    async def _arun(self, request: ReadDesktop, control: Control) -> tuple[ReadDesktop, Record]:
        return (request, await self.capture.arun(DesktopReadRequest(), control))

    def _run(self, value: ReadDesktop, control: Control) -> tuple[ReadDesktop, Record]:
        raise TypeError("Desktop reads require arun()")


class DesktopReadRecovery(Node["ReadDesktop", "Observation"]):
    """Clear desktop observation state when its read flow fails.

    Run desktop capture and publication, then reset retained desktop metadata and discard
    pending observations if an exception escapes. Propagate the original failure to the
    caller.

    Attributes:
        controller: Session owner of desktop metadata and pending observation events.
        body: Desktop capture, retention, and publication flow wrapped by this failure
            boundary.
    """

    def __init__(self, controller: SessionController, body: Step[ReadDesktop, Observation]) -> None:
        self.controller, self.body, self.children = (controller, body, (body,))
        super().__init__("DesktopReadRecovery", body.contract)

    async def _arun(self, request: ReadDesktop, control: Control) -> Observation:
        try:
            return await self.body.arun(request, control)
        except BaseException:
            self.controller.desktop.reset()
            pending_observations_discarded(self.controller)
            raise

    def _run(self, value: ReadDesktop, control: Control) -> Observation:
        raise TypeError("Desktop reads require arun()")


class ControllerDesktopReadPipeline(HostOperation):
    """Capture and publish a desktop observation.

    Collect native desktop pixels, retain their coordinate metadata, and emit the public
    observation. Apply the host-operation boundary and desktop-state cleanup to the complete
    flow.
    """

    def __init__(self, controller: SessionController) -> None:
        body = Sequence(
            name="ControllerDesktopReadPipeline",
            steps=[
                CollectDesktop(controller),
                record_desktop_observation(desktop=controller.desktop, observation_state=controller._observation_state),
                publish_desktop_observation(publish_observation=controller.publish_observation),
            ],
        )
        super().__init__(DesktopReadRecovery(controller, body))
