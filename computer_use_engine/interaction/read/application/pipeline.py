"""Application observation: validate, acquire, remember and publish."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.diagnostics import localFailure
from computer_use.interaction.operation import HostOperation
from computer_use.interaction.read.app_read.model import ReadRequest
from computer_use.interaction.read.application.model import AppReadResult, AppReadState
from computer_use.interaction.read.application.passes.publish_window_choices import (
    stage as publish_window_choices,
)
from computer_use.interaction.read.application.passes.remember_application_observation import (
    stage as remember_application_observation,
)
from computer_use.interaction.read.application.passes.resolve_application_state import (
    stage as resolve_application_state,
)
from computer_use.interaction.read.application.passes.select_application_read_result import (
    stage as select_application_read_result,
)
from computer_use.interaction.read.model import WindowSelectionRequired
from computer_use.interaction.read.publication.pipeline import ObservationPublicationPipeline
from computer_use.native.scopes import RunningApplication, WorkingWindow
from computer_use.pipeline import Contract, Select, Sequence
from computer_use.pipeline.core import Node
from computer_use.session.events import pendingObservationsDiscarded

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.pipeline.core import Control


class AcquireApplicationObservation(Node["AppReadState", "AppReadResult"]):
    """Acquire application evidence for the controller read.

    Resolve the request against its session state and run application observation. Return
    the captured evidence or a window-selection response alongside the original state.

    Attributes:
        controller: Session owner used to construct and reconcile application observation
            requests.
        acquisition: Application observation flow providing window or menu evidence.
    """

    def __init__(self, controller: SessionController) -> None:
        from computer_use.interaction.read.pipeline import ApplicationObservationPipeline

        self.controller = controller
        self.acquisition = ApplicationObservationPipeline(controller.reader)
        self.children = (self.acquisition,)
        super().__init__(
            "AcquireApplicationObservation",
            Contract(AppReadState, AppReadResult, self.acquisition.contract.effects),
        )

    async def _arun(self, value: AppReadState, control: Control) -> AppReadResult:
        controller, state = (self.controller, value.state)

        def remember_scope(scope: RunningApplication | WorkingWindow) -> None:
            if isinstance(scope, WorkingWindow):
                state.selection = scope
                scope = RunningApplication(scope.application, scope.name, scope.root.session)
            state.running = scope

        try:
            if state.running is not None and state.running.session != controller.transport.session:
                raise localFailure(
                    "application belongs to an expired driver session",
                    "obtain a new App from this Computer",
                    "stale_target",
                )
            if state.query_target is not None:
                controller.validateElement(state.query_target)
            observed = await self.acquisition.arun(
                ReadRequest(
                    state.selection or state.running or state.selector,
                    menu_path=value.request.menu_path,
                    on_scope=remember_scope,
                ),
                control,
            )
        except WindowSelectionRequired as choices:
            return AppReadResult(value, choices, True)
        except BaseException:
            state.observation = None
            pendingObservationsDiscarded(controller)
            raise
        return AppReadResult(value, observed)

    def _run(self, value: AppReadState, control: Control) -> AppReadResult:
        raise TypeError("Application reads require arun()")


class ControllerApplicationReadPipeline(HostOperation):
    """Read an application scope and publish its observation.

    Pair the request with session state, acquire the application evidence, and select normal
    observation or window-choice delivery. Retain the observed catalogues, snapshots, and
    image bindings before publication.
    """

    def __init__(self, controller: SessionController) -> None:
        super().__init__(
            Sequence(
                name="ControllerApplicationReadPipeline",
                steps=[
                    resolve_application_state(state_of=controller.state),
                    AcquireApplicationObservation(controller),
                    Select(
                        name="ApplicationReadResult",
                        selector=select_application_read_result(),
                        branches={
                            True: publish_window_choices(publish_choices=controller._choices),
                            False: Sequence(
                                name="RememberApplicationObservation",
                                steps=[
                                    remember_application_observation(
                                        catalogue=controller._catalogue,
                                        register_window_image=controller._image,
                                        remember_snapshot=controller._remember,
                                    ),
                                    ObservationPublicationPipeline(controller),
                                ],
                            ),
                        },
                    ),
                ],
            )
        )
