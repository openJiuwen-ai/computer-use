# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Directory observation adapter; identity matching is shared in native.query."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.native.query.directory import (
    SurfaceReadProfile,
    select_application,
    select_surface,
    selected_window,
    surfaces_query,
)

__all__ = ["NativeDirectory", "select_application", "select_surface", "selected_window"]

if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.protocol.records import Record


class NativeDirectory:
    """Read application and surface catalogues through bounded directory pipelines."""

    def __init__(self, port: NativePort) -> None:
        self.port = port

    async def applications(
        self, *, selector: str | None = None, timeout_ms: int = 30000, include_ended: bool = False
    ) -> DirectoryFacts:
        query: Record = {"kind": "applications"}
        if include_ended:
            query["includeEnded"] = True
        return await self._query(query, timeout_ms, application_selector=selector)

    async def applications_for_window_title(self, title: str) -> DirectoryFacts:
        """Observe applications with an exact native window-caption match."""
        return await self._query({"kind": "applications", "windowTitle": title}, 30000)

    async def window_catalogue(self, app_name: str) -> DirectoryFacts:
        return await self._query(dict(kind="windowCatalogue", appName=app_name), 30000)

    async def surfaces(self, application: str, *, timeout_ms: int = 30000) -> DirectoryFacts:
        if not isinstance(application, str) or not application:
            raise ValueError("An observed running-application id is required")
        return await self._query(dict(kind="surfaces", application=application), timeout_ms)

    async def scoped_surfaces(
        self,
        application: str,
        *,
        profile: SurfaceReadProfile,
        capabilities: frozenset[str],
        root_window: str | None = None,
    ) -> DirectoryFacts:
        """Use the declared surface-read isolation for a retained application scope."""
        query = surfaces_query(application, profile=profile, capabilities=capabilities, root_window=root_window)
        return await self._query(query, 30000)

    async def surface_batch(
        self,
        applications: list[str],
        *,
        profile: SurfaceReadProfile = SurfaceReadProfile.ACCESSIBILITY,
        timeout_ms: int = 30000,
    ) -> DirectoryFacts:
        """Read ordered per-application directories through the negotiated batch endpoint."""
        if not isinstance(profile, SurfaceReadProfile):
            raise ValueError("A typed surface read profile is required")
        query = dict(kind="surfaceBatch", applications=applications)
        if profile is SurfaceReadProfile.VISUAL and "nativeVisualSurfaceBatch" in self.port.connection.capabilities:
            query["profile"] = "visual"
        return await self._query(query, timeout_ms)

    async def _query(
        self, query: Record, timeout_ms: int, *, application_selector: str | None = None
    ) -> DirectoryFacts:
        from computer_use.interaction.read.acquisition.directory.model import DirectoryRequest
        from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryReadPipeline

        return await DirectoryReadPipeline(self.port).arun(DirectoryRequest(query, timeout_ms, application_selector))

    async def collect(
        self, read: OperationLease[Binding], query: Record, *, _control: Control | None = None
    ) -> DirectoryFacts:
        """Directory facts never expand a window-scoped operation to a session."""
        from computer_use.interaction.read.acquisition.directory.model import DirectoryPlan
        from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryCollectionPipeline

        return await DirectoryCollectionPipeline(self.port).arun(DirectoryPlan(read, query), _control)
