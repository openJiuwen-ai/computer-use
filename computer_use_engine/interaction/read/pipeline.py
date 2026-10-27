"""Application read entry: preparation, branch acquisition, and final delivery.

AppReadPreparationPipeline only returns its selected scope. This parent owns
continuation to menu or window observation, never the preparation child.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from computer_use.interaction.control import scope_control
from computer_use.interaction.read.acquisition.pipeline import AcquisitionPipeline
from computer_use.interaction.read.app_read.model import (
    ApplicationSelection,
    ReadCall,
    ReadInvocation,
    _StartupReceipt,
)
from computer_use.interaction.read.app_read.pipeline import AppReadPreparationPipeline
from computer_use.interaction.read.delivery.pipeline import AppDeliveryPipeline
from computer_use.interaction.read.numbering.observation.pipeline import (
    NumberedPresentationPipeline,
)
from computer_use.interaction.read.passes.parse_read_request import stage as parse_read_request
from computer_use.interaction.read.passes.plan_read_components import stage as plan_read_components
from computer_use.interaction.read.passes.select_read_components import (
    stage as select_read_components,
)
from computer_use.pipeline import Contract, Pipeline, Scope, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from typing import AsyncIterator, NoReturn

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.app_read.model import ReadRequest
    from computer_use.interaction.read.model import AppObservation, MenuAppObservation
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Control, Step


class WindowObservationPipeline(Pipeline["ApplicationSelection", "AppObservation"]):
    """Observe and present the selected working window.

    Collect and recheck the requested window components, then number and render their
    snapshots. Assemble the result into an application observation with its native bindings
    and collection evidence.

    Attributes:
        services: Application reader providing component collection, display numbering, and
            observation formatting.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ApplicationSelection, AppObservation]:
        services = self.services
        return Sequence(
            name="ReadPipeline",
            steps=[
                AcquisitionPipeline(services),
                NumberedPresentationPipeline(services),
                AppDeliveryPipeline(services),
            ],
        )


class _ReadCallBody(Node["ReadCall", "AppObservation | MenuAppObservation"]):
    """Prepare the application scope and run its selected observation branch.

    Resolve the application or startup request, then collect either menu sources or the
    chosen working window. Run the child flow under the call's remaining deadline.

    Attributes:
        services: Application reader used to resolve scope and collect menu or window
            evidence.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services

        async def menu(value: ApplicationSelection, control: Control) -> MenuAppObservation:
            request = value.request
            # The pipeline shares retained bindings with their owning service.
            # pylint: disable-next=protected-access
            return await services._menu_observation(
                value.application,
                value.selection,
                value.directory,
                request.menu_path or (),
                value.launch,
                value.call.remaining,
                fields=request.fields,
                max_nodes=request.max_nodes,
                max_depth=request.max_depth,
                _control=control,
            )

        from computer_use.interaction.read.delivery.menu.pipeline import MenuObservationPipeline

        class MenuReadBranch(Node):
            """Collect menus for the application selected by read preparation.

            Pass the resolved application, optional working window, menu path, and collection limits
            to menu observation. Return the formatted menu evidence to the application-read flow.
            """

            def __init__(self) -> None:
                self.children = (MenuObservationPipeline(services),)
                super().__init__(
                    "ReadApplicationMenu",
                    Contract(ApplicationSelection, object, self.children[0].contract.effects),
                )

            async def _arun(self, value: ApplicationSelection, control: Control) -> MenuAppObservation:
                return await menu(value, control)

            def _run(self, value: ApplicationSelection, control: Control) -> NoReturn:
                raise TypeError("Menu reads require arun()")

        selection = Select(
            name="ObservedReadBranch",
            selector=select_read_components(),
            branches={
                True: MenuReadBranch(),
                False: Sequence(
                    name="WindowReadPipeline",
                    steps=[plan_read_components(), WindowObservationPipeline(services)],
                ),
            },
        )
        self.body = Sequence(name="ReadRequestPipeline", steps=[AppReadPreparationPipeline(services), selection])
        super().__init__("ReadCallBody", self.body.contract)

    async def _arun(self, call: ReadCall, control: Control) -> AppObservation | MenuAppObservation:
        return await self.body.arun(call, scope_control(self.services.port, call.remaining, control))

    def _run(self, value: ReadCall, control: Control) -> AppObservation | MenuAppObservation:
        raise TypeError("App reads require arun()")


class ApplicationObservationPipeline(Node["ReadRequest", "AppObservation | MenuAppObservation"]):
    """Run an application read under one call deadline.

    Parse the request and enter a call scope before preparing and observing the application.
    Preserve a launch receipt on subsequent failures so the caller can distinguish startup
    from observation.

    Attributes:
        services: Application reader providing the native call scope and startup-error
            annotation.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services

        # Only the launch receipt is mutable across a failed call. Its owner
        # annotates subsequent errors after every native lease has exited.
        @asynccontextmanager
        async def claim(invocation: ReadInvocation, control: Control) -> AsyncIterator[ReadCall]:
            with services.port.calls.claim(invocation.request.timeout_ms) as remaining:
                yield ReadCall(invocation.request, remaining, invocation.receipt)

        self.body = Sequence(
            name="AppReadCoordinator",
            steps=[
                parse_read_request(),
                Scope(name="CallScope", using=claim, body=_ReadCallBody(services)),
            ],
        )
        super().__init__("AppRead", self.body.contract)

    async def _arun(self, request: ReadRequest, control: Control) -> AppObservation | MenuAppObservation:
        receipt = _StartupReceipt()
        try:
            return await self.body.arun(ReadInvocation(request, receipt), control)
        except BaseException as error:
            if receipt.launch is not None:
                # pylint: disable-next=protected-access
                annotated = self.services._after_launch(error, receipt.launch)
                if annotated is not error:
                    raise annotated from error
            raise

    def _run(self, value: ReadRequest, control: Control) -> AppObservation | MenuAppObservation:
        raise TypeError("App reads require arun()")


class ReadPipeline(Node["InteractionRequest", "ReadResult"]):
    """Enter the session's observation flows.

    Delegate the typed read request to application, desktop, element, or captured-text
    handling. Return the result produced by the selected branch.
    """

    def __init__(self, controller: SessionController) -> None:
        from computer_use.interaction.read.requests.pipeline import SessionReadPipeline

        self.body = SessionReadPipeline(controller)
        self.children = (self.body,)
        super().__init__("ReadPipeline", self.body.contract)

    async def _arun(self, value: InteractionRequest, control: Control) -> ReadResult:
        return await self.body.arun(value, control)

    def _run(self, value: InteractionRequest, control: Control) -> ReadResult:
        raise TypeError("System reads require arun()")


if TYPE_CHECKING:
    from computer_use.interaction.records import ReadResult
