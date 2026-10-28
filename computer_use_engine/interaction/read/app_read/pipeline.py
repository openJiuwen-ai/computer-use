"""Read request parsing, fixed scope selection and explicit startup coordination.

Launching is a write branch before ReadPipeline. A failed observation never
retargets a previously fixed working root or silently repeats startup input.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.choices.pipeline import WindowChoiceServiceNode
from computer_use.interaction.read.acquisition.directory.service import (
    select_application,
    selected_window,
)
from computer_use.interaction.read.acquisition.scope.pipeline import (
    DirectoryServiceNode,
    WorkingScopeServiceNode,
)
from computer_use.interaction.read.app import RunningApplication, WorkingWindow, failure
from computer_use.interaction.read.app_read.model import (
    ApplicationSelection,
    DirectoryLease,
    ReadCall,
)
from computer_use.interaction.read.app_read.passes.bind_read_scope import stage as bind_read_scope
from computer_use.interaction.read.app_read.passes.remember_running_scope import (
    stage as remember_running_scope,
)
from computer_use.interaction.read.app_read.passes.remember_starting_scope import (
    stage as remember_starting_scope,
)
from computer_use.interaction.read.app_read.passes.retain_running_application import (
    stage as retain_running_application,
)
from computer_use.interaction.read.app_read.passes.select_explicit_startup import (
    stage as select_explicit_startup,
)
from computer_use.interaction.read.app_read.passes.select_identity_source import (
    stage as select_identity_source,
)
from computer_use.interaction.read.app_read.passes.validate_explicit_startup import (
    stage as validate_explicit_startup,
)
from computer_use.interaction.read.app_read.startup.pipeline import StartupServiceNode
from computer_use.native.contracts import ApplicationBinding, Dispatch, Intent
from computer_use.native.query.directory import surfaces_query
from computer_use.pipeline import Contract, Effect, Pipeline, Scope, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from typing import Any, AsyncIterator

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control, Step


class DirectorySelectionPipeline(Pipeline["ReadCall", "ApplicationSelection"]):
    """Resolve an application selector against native directory evidence.

    Use a retained running identity or the application catalogue to select the application.
    Collect its surfaces when running and return the selection with the observed catalogues.

    Attributes:
        services: Application reader providing directory queries and native operation
            scopes.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ReadCall, ApplicationSelection]:
        services = self.services

        @asynccontextmanager
        async def lease(call: ReadCall, control: Control) -> AsyncIterator[DirectoryLease]:
            async with services.port.calls.operation(None, call.remaining) as read:
                yield DirectoryLease(call, read)

        async def resolve(value: Any, control: Control) -> DirectoryLease:
            request, read = (value.call.request, value.read)
            selector, window = (request.selector, request.window)
            applications = None
            if isinstance(selector, RunningApplication):
                if read.target.session != selector.session:
                    raise failure("stale_target", "The selected application belongs to a previous driver session.")
                application = dict(id=selector.application, displayName=selector.name, isRunning=True)
            else:
                applications = await services.directory.collect(read, dict(kind="applications"), _control=control)
                application = select_application(applications, selector)
                chosen_window = selected_window(application, selector)
                if chosen_window is not None:
                    if window is not None and window != chosen_window:
                        raise failure(
                            "invalid_request",
                            "A window selector cannot select a different working window.",
                        )
                    window = chosen_window
            call = replace(value.call, request=replace(request, window=window))
            return DirectoryLease(call, read, application, applications)

        async def surfaces(value: Any, control: Control) -> ApplicationSelection:
            directory = (
                await services.directory.collect(
                    value.read,
                    surfaces_query(
                        value.application["id"],
                        accessibility=value.call.request.view != "image",
                        capabilities=services.port.connection.capabilities,
                    ),
                    _control=control,
                )
                if value.application["isRunning"]
                else None
            )
            return ApplicationSelection(value.call, value.application, directory, value.applications)

        return Scope(
            name="DirectorySelectionScope",
            using=lease,
            body=Sequence(
                name="DirectorySelectionPipeline",
                steps=[
                    DirectoryServiceNode(
                        "ResolveApplicationIdentity",
                        services.directory,
                        resolve,
                        DirectoryLease,
                        DirectoryLease,
                    ),
                    remember_running_scope(),
                    DirectoryServiceNode(
                        "CollectApplicationSurfaces",
                        services.directory,
                        surfaces,
                        DirectoryLease,
                        ApplicationSelection,
                    ),
                ],
            ),
        )


class _LaunchDispatch(Node["ApplicationSelection", "ApplicationSelection"]):
    """Dispatch the selected application startup.

    Run the launch intent inside the existing read call and retain its immutable receipt.
    Update the selected application from that receipt and carry the installation identity
    when a launch was dispatched.

    Attributes:
        services: Application reader supplying the native write port and enclosing read
            budget.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        self.children = ()
        super().__init__("LaunchDispatch", Contract(ApplicationSelection, ApplicationSelection, frozenset(Effect)))

    async def _arun(self, value: ApplicationSelection, control: Control) -> ApplicationSelection:
        from computer_use.interaction.write.pipeline import WritePipeline

        pipeline = WritePipeline(self.services.port)
        self.children = (pipeline.intent, pipeline.main)
        try:
            receipt = await pipeline.run_in_call(
                Intent("launch"),
                ApplicationBinding(value.applications["session"], value.application["id"]),
                value.call.remaining,
                _control=control,
            )
            value.call.receipt.launch = MappingProxyType(
                {**receipt, "application": MappingProxyType(dict(receipt["application"]))}
            )
            installation = value.installation if receipt["dispatch"] == Dispatch.DISPATCHED.value else None
            return replace(value, application=value.launch["application"], installation=installation)
        finally:
            self.children = ()

    def _run(self, value: ApplicationSelection, control: Control) -> ApplicationSelection:
        raise TypeError("Application startup requires arun()")


class LaunchCoordinator(Pipeline["ApplicationSelection", "ApplicationSelection"]):
    """Launch an allowed application and observe its running scope.

    Validate the startup request, retain the dispatch and starting-scope identity, then wait
    for its application directory. Return the selection updated with the running application
    and surfaces.

    Attributes:
        services: Application reader providing launch dispatch and bounded startup
            discovery.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ApplicationSelection, ApplicationSelection]:
        services = self.services

        async def startup(value: Any, control: Control) -> Any:
            # The pipeline shares retained bindings with their owning service.
            # pylint: disable-next=protected-access
            application, directory = await services._startup_directory(
                value.application,
                value.applications["session"],
                value.call.remaining,
                launched=value.launch["dispatch"] == Dispatch.DISPATCHED.value,
                installation=value.installation,
                on_scope=value.request.on_scope,
                _control=control,
            )
            return replace(value, application=application, directory=directory)

        return Sequence(
            name="LaunchCoordinator",
            steps=[
                validate_explicit_startup(),
                _LaunchDispatch(services),
                remember_starting_scope(),
                StartupServiceNode(
                    "ObserveStartedApplication",
                    services,
                    startup,
                    ApplicationSelection,
                    ApplicationSelection,
                ),
            ],
        )


class AppReadPreparationPipeline(Pipeline["ReadCall", "ApplicationSelection"]):
    """Resolve the application instance and working scope for observation.

    Use fixed-window, ongoing-startup, or directory evidence to establish identity and
    perform an allowed startup when needed. Select and retain the working window, or return
    the application context used for menu-only observation.

    Attributes:
        services: Application reader providing identity discovery, startup, and window-
            choice responses.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ReadCall, ApplicationSelection]:
        services = self.services

        async def bound(call: ReadCall, control: Control) -> ApplicationSelection:
            # pylint: disable-next=protected-access
            selection, directory = await services._discover(
                call.request.selector,
                call.remaining,
                _control=control,
                **(dict(accessibility=False) if call.request.view == "image" else {}),
            )
            return ApplicationSelection(
                call,
                dict(id=selection.application, displayName=selection.name),
                directory,
                selection=selection,
            )

        async def starting(call: ReadCall, control: Control) -> ApplicationSelection:
            selector = cast("StartingApplication", call.request.selector)
            # pylint: disable-next=protected-access
            application, directory = await services._startup_directory(
                selector.launch["application"],
                selector.session,
                call.remaining,
                launched=True,
                installation=selector.installation,
                on_scope=call.request.on_scope,
                _control=control,
            )
            return ApplicationSelection(call, application, directory)

        async def choose(value: Any, control: Control) -> Any:
            if isinstance(value.request.selector, WorkingWindow):
                return value
            directory, application, window = (value.directory, value.application, value.request.window)
            selection = None
            if directory["surfaces"] or "nativeApplicationRoots" not in services.port.connection.capabilities:
                try:
                    # pylint: disable-next=protected-access
                    selection = services._selection(application["id"], application["displayName"], directory, window)
                except ComputerUseError as error:
                    if (
                        window is None
                        and directory["surfaces"]
                        and (error.code in ("ambiguous_target", "observation_incomplete"))
                    ):
                        # pylint: disable-next=protected-access
                        raise await services._window_choices(
                            error,
                            application["id"],
                            application["displayName"],
                            directory,
                            value.call.remaining,
                            _control=control,
                            include_overview=value.request.overview,
                        ) from error
                    raise
            elif window is not None:
                raise failure("stale_target", "The selected working window is not in the current directory.")
            return replace(value, selection=selection)

        identity = Select(
            name="ApplicationIdentitySource",
            selector=select_identity_source(),
            branches={
                "fixed": WorkingScopeServiceNode(
                    "DiscoverFixedWorkingRoot", services, bound, ReadCall, ApplicationSelection
                ),
                "starting": StartupServiceNode(
                    "ObserveExistingStartup", services, starting, ReadCall, ApplicationSelection
                ),
                "directory": DirectorySelectionPipeline(services),
            },
        )
        startup = Select(
            name="OptionalStartup",
            selector=select_explicit_startup(),
            branches={True: LaunchCoordinator(services), False: retain_running_application()},
        )
        return Sequence(
            name="ApplicationSelectionPipeline",
            steps=[
                identity,
                startup,
                WindowChoiceServiceNode(
                    "SelectWorkingScope", services, choose, ApplicationSelection, ApplicationSelection
                ),
                bind_read_scope(),
            ],
        )


if TYPE_CHECKING:
    from computer_use.native.scopes import StartingApplication
