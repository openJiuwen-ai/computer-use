# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application read entry: preparation, branch acquisition, and final delivery.

AppReadPreparationPipeline only returns its selected scope. This parent owns
continuation to menu or window observation, never the preparation child.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.control import scope_control
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.pipeline import AcquisitionPipeline
from computer_use.interaction.read.app_read.model import (
    ApplicationSelection,
    ReadCall,
    ReadComponentKind,
    ReadInvocation,
    _StartupReceipt,
)
from computer_use.interaction.read.app_read.pipeline import AppReadPreparationPipeline
from computer_use.interaction.read.delivery.pipeline import AppDeliveryPipeline
from computer_use.interaction.read.limits import DEFAULT_MENU_MAX_NODES
from computer_use.interaction.read.numbering.observation.pipeline import NumberedPresentationPipeline
from computer_use.interaction.read.passes.parse_read_request import stage as parse_read_request
from computer_use.interaction.read.passes.plan_read_components import stage as plan_read_components
from computer_use.interaction.read.passes.reject_windowless_image import stage as reject_windowless_image
from computer_use.interaction.read.passes.select_read_components import stage as select_read_components
from computer_use.pipeline import Contract, Pipeline, Scope, Select, Sequence
from computer_use.pipeline.core import Node
from computer_use.session.operations import OperationDeadlineExceeded, current_operation_budget

if TYPE_CHECKING:
    from typing import AsyncIterator, NoReturn

    from computer_use.interaction.read.acquisition.model import AcquiredObservation
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.app_read.model import ReadRequest
    from computer_use.interaction.read.model import AppObservation, MenuAppObservation
    from computer_use.interaction.read.numbering.observation.model import RenderedObservation
    from computer_use.pipeline.core import Control, Step


class _PrepareCapturedObservation(Node["AcquiredObservation", "RenderedObservation"]):
    """Use the operation's CPU budget after native acquisition has finished.

    A shorter component lease can expire after returning useful partial facts.
    Its clock controls further native acquisition, not whether those returned
    facts may be numbered and presented. The SDK operation clock remains the
    single authority for CPU work, with original cancellation metadata retained.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.presentation = NumberedPresentationPipeline(services)
        self.children = (self.presentation,)
        super().__init__("PrepareCapturedObservation", self.presentation.contract)

    def _run(self, value: AcquiredObservation, control: Control) -> RenderedObservation:
        budget = current_operation_budget()
        cpu_control = replace(control, remaining=budget.remaining if budget is not None else None)
        try:
            return self.presentation.run(value, cpu_control)
        except OperationDeadlineExceeded:
            # Acquisition already completed under this exact binding. A CPU
            # cutoff must not discard those facts or independent pixels, nor
            # expose the source's internal references as public aliases.
            from computer_use.interaction.read.numbering.observation.model import RenderedObservation

            gap = ObservationGap(
                "presentation", "observation_incomplete",
                "Captured facts retained with their original scope and times; display numbering and tree "
                "presentation did not complete within the original operation budget.",
            )
            return RenderedObservation(replace(value, gaps=(*value.gaps, gap)), None, snapshots_numbered=False)

    async def _arun(self, value: AcquiredObservation, control: Control) -> RenderedObservation:
        return self._run(value, control)


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
                _PrepareCapturedObservation(services),
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
                max_nodes=DEFAULT_MENU_MAX_NODES if request.max_nodes is None else request.max_nodes,
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
                ReadComponentKind.MENU: MenuReadBranch(),
                ReadComponentKind.IMAGE_UNAVAILABLE: reject_windowless_image(),
                ReadComponentKind.WINDOW: Sequence(
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
