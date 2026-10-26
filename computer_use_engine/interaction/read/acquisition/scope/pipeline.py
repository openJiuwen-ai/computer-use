"""Fixed-root discovery composes a read lease, directory query and pure choice."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryCollectionPipeline
from computer_use.interaction.read.acquisition.scope.model import _ObservedDirectory, _ScopeLease
from computer_use.interaction.read.acquisition.scope.passes.select_fixed_root import (
    stage as select_fixed_root,
)
from computer_use.interaction.read.acquisition.scope.passes.validate_fixed_session import (
    stage as validate_fixed_session,
)
from computer_use.native.query.directory import surfaces_query
from computer_use.pipeline import Contract, Effect, Pipeline, Scope, Sequence
from computer_use.pipeline.core import Node, T, U

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Awaitable, Callable

    from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
    from computer_use.interaction.read.acquisition.scope.model import WorkingScopeRequest
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.native.scopes import WorkingWindow
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


class DirectoryServiceNode(Node[T, U]):
    """Connect a directory-query callback to the shared collection graph.

    Invoke the supplied callback with its stage data and control. Expose the bounded
    directory collection flow associated with that service.

    Attributes:
        function: Bound callback carrying the stage-specific directory query and result
            conversion.
    """

    def __init__(
        self,
        name: str,
        directory: NativeDirectory,
        function: Callable[[T, Control], Awaitable[U]],
        input_type: Any = object,
        output_type: Any = object,
    ) -> None:
        self.function = function
        self.children = (DirectoryCollectionPipeline(directory.port),)
        super().__init__(name, Contract(input_type, output_type, {Effect.QUERY, Effect.RESOURCE}))

    async def _arun(self, value: T, control: Control) -> U:
        return await self.function(value, control)

    def _run(self, value: T, control: Control) -> U:
        raise TypeError("Directory queries require arun()")


class WorkingScopeDiscoveryPipeline(Pipeline["WorkingScopeRequest", "tuple[WorkingWindow, DirectoryFacts]"]):
    """Reconcile an established working window with the observed directory.

    Collect the application's current surfaces using the requested accessibility profile,
    then resolve the fixed root against them. Return its working-window identity and the
    observed catalogue.

    Attributes:
        services: Application reader supplying directory queries and fixed-root resolution.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[WorkingScopeRequest, tuple[WorkingWindow, DirectoryFacts]]:
        services = self.services

        @asynccontextmanager
        async def lease(request: WorkingScopeRequest, control: Control) -> AsyncIterator[_ScopeLease]:
            async with services.port.calls.operation(None, cast("Callable[[], int]", control.remaining)) as read:
                yield _ScopeLease(request, read)

        async def collect(value: Any, control: Control) -> _ObservedDirectory:
            query = surfaces_query(
                value.request.selection.application,
                accessibility=value.request.accessibility,
                capabilities=services.port.connection.capabilities,
            )
            directory = await services.directory.collect(value.read, query, _control=control)
            return _ObservedDirectory(value.request, directory)

        inside = Sequence(
            name="DiscoverFixedDirectory",
            steps=[
                validate_fixed_session(),
                DirectoryServiceNode(
                    "CollectFixedDirectory",
                    services.directory,
                    collect,
                    _ScopeLease,
                    _ObservedDirectory,
                ),
            ],
        )
        return Sequence(
            name="WorkingScopeDiscoveryPipeline",
            steps=[
                Scope(
                    name="WorkingScopeDirectoryScope",
                    using=lease,
                    body=NativeScopeBody(services.port, inside, lambda value: value.read.remaining),
                ),
                select_fixed_root(services=services),
            ],
        )


class WorkingScopeServiceNode(Node[T, U]):
    """Connect a working-scope callback to its declared discovery graph.

    Invoke the callback with its stage data and execution control. Expose working-window
    discovery as the callback's child flow.

    Attributes:
        function: Bound callback adapting stage inputs to working-scope discovery.
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
        self.children = (WorkingScopeDiscoveryPipeline(services),)
        super().__init__(name, Contract(input_type, output_type, {Effect.QUERY, Effect.RESOURCE}))

    async def _arun(self, value: T, control: Control) -> U:
        return await self.function(value, control)

    def _run(self, value: T, control: Control) -> U:
        raise TypeError("Scope discovery requires arun()")
