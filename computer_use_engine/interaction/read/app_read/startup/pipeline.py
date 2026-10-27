"""Observe an already dispatched startup with the existing five-round budget.

Only catalogues and their declared associations may change the selected running
identity. This loop never launches again or infers a wrapper's child process.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.scope.pipeline import DirectoryServiceNode
from computer_use.interaction.read.app_read.startup.model import StartupAttempt, StartupLease
from computer_use.interaction.read.app_read.startup.passes.remember_associated_running_scope import (
    stage as remember_associated_running_scope,
)
from computer_use.interaction.read.app_read.startup.passes.resolve_declared_startup_association import (
    stage as resolve_declared_startup_association,
)
from computer_use.interaction.read.app_read.startup.passes.retain_started_identity import (
    stage as retain_started_identity,
)
from computer_use.interaction.read.app_read.startup.passes.startup_directory_gate import (
    stage as startup_directory_gate,
)
from computer_use.interaction.read.app_read.startup.passes.startup_recipe_unresolved import (
    stage as startup_recipe_unresolved,
)
from computer_use.interaction.read.app_read.startup.passes.validate_startup_session import (
    stage as validate_startup_session,
)
from computer_use.interaction.read.app_read.startup.passes.wait_startup_catalogue import (
    stage as wait_startup_catalogue,
)
from computer_use.pipeline import Contract, Pipeline, Repeat, Scope, Select, Sequence
from computer_use.pipeline.core import Node, T, U

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Awaitable, Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.app_read.startup.model import StartupDirectoryState
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import ApplicationInfo
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


class StartupDirectoryPipeline(Pipeline["StartupDirectoryState", "tuple[ApplicationInfo, DirectoryFacts]"]):
    """Discover the running scope associated with an application startup.

    Collect application and surface evidence, resolve a declared installation association,
    and notify the scope owner when confirmed. Use the startup gate to finish with a
    catalogue or continue its bounded discovery attempts.

    Attributes:
        services: Application reader providing directory queries and startup identity
            resolution.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[StartupDirectoryState, tuple[ApplicationInfo, DirectoryFacts]]:
        services = self.services

        @asynccontextmanager
        async def lease(state: StartupDirectoryState, control: Control) -> AsyncIterator[StartupLease]:
            async with services.port.calls.operation(None, cast("Callable[[], int]", control.remaining)) as read:
                yield StartupLease(state, read)

        async def applications(value: Any, control: Control) -> Any:
            observed = await services.directory.collect(value.read, dict(kind="applications"), _control=control)
            return replace(value, applications=observed)

        async def surfaces(value: Any, control: Control) -> StartupAttempt:
            stale, directory = None, None
            try:
                directory = await services.directory.collect(
                    value.read,
                    dict(kind="surfaces", application=value.state.application["id"]),
                    _control=control,
                )
            except ComputerUseError as error:
                if value.state.installation is None or error.code != "stale_target" or error.effect != "none":
                    raise
                stale = error
            return StartupAttempt(value.state, directory, stale)

        associate = Select(
            name="StartupAssociation",
            selector=startup_recipe_unresolved(),
            branches={
                True: Sequence(
                    name="ObserveDeclaredStartupAssociation",
                    steps=[
                        DirectoryServiceNode(
                            "CollectStartupApplications",
                            services.directory,
                            applications,
                            StartupLease,
                            StartupLease,
                        ),
                        resolve_declared_startup_association(),
                        remember_associated_running_scope(),
                    ],
                ),
                False: retain_started_identity(),
            },
        )
        query = Sequence(
            name="StartupDirectoryQuery",
            steps=[
                validate_startup_session(),
                associate,
                DirectoryServiceNode(
                    "CollectStartupSurfaces", services.directory, surfaces, StartupLease, StartupAttempt
                ),
            ],
        )
        body = Sequence(
            name="StartupDirectoryAttempt",
            steps=[
                wait_startup_catalogue(),
                Scope(
                    name="StartupDirectoryScope",
                    using=lease,
                    body=NativeScopeBody(services.port, query, lambda value: value.read.remaining),
                ),
            ],
        )
        return Repeat(name="StartupDirectoryPipeline", body=body, gate=startup_directory_gate(), max_iterations=5)


class StartupServiceNode(Node[T, U]):
    """Connect a startup service callback to its declared child graph.

    Invoke the supplied callback with the stage inputs and execution control. Expose startup
    directory discovery as its child flow for execution diagnostics.

    Attributes:
        function: Bound callback that adapts the enclosing read stage to startup discovery.
    """

    def __init__(
        self,
        name: str,
        services: NativeAppReader,
        function: Callable[[T, Control], Awaitable[U]],
        input_type: Any = object,
        output_type: Any = object,
    ) -> None:
        self.function = function
        self.children = (StartupDirectoryPipeline(services),)
        super().__init__(name, Contract(input_type, output_type, self.children[0].contract.effects))

    async def _arun(self, value: T, control: Control) -> U:
        return await self.function(value, control)

    def _run(self, value: T, control: Control) -> U:
        raise TypeError("Startup observation requires arun()")
