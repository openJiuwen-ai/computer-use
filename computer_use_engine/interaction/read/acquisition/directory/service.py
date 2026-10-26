"""Directory observation adapter; identity matching is shared in native.query."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.native.query.directory import (
    select_application as select_application,
    select_surface as select_surface,
    selected_window as selected_window,
)

if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts, Record


class NativeDirectory:
    """Read application and surface catalogues through bounded directory pipelines."""

    def __init__(self, port: NativePort) -> None:
        self.port = port

    async def applications(self, *, timeout_ms: int = 30000) -> DirectoryFacts:
        return await self._query(dict(kind="applications"), timeout_ms)

    async def surfaces(self, application: str, *, timeout_ms: int = 30000) -> DirectoryFacts:
        if not isinstance(application, str) or not application:
            raise ValueError("An observed running-application id is required")
        return await self._query(dict(kind="surfaces", application=application), timeout_ms)

    async def _query(self, query: Record, timeout_ms: int) -> DirectoryFacts:
        from computer_use.interaction.read.acquisition.directory.model import DirectoryRequest
        from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryReadPipeline

        return await DirectoryReadPipeline(self.port).arun(DirectoryRequest(query, timeout_ms))

    async def collect(
        self, read: OperationLease[Binding], query: Record, *, _control: Control | None = None
    ) -> DirectoryFacts:
        """Directory facts never expand a window-scoped operation to a session."""
        from computer_use.interaction.read.acquisition.directory.model import DirectoryPlan
        from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryCollectionPipeline

        return await DirectoryCollectionPipeline(self.port).arun(DirectoryPlan(read, query), _control)
