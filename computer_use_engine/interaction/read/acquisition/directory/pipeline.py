# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Independent directory reads never widen a window-scoped native lease."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.directory.model import DirectoryPlan
from computer_use.interaction.read.acquisition.directory.passes.collect_directory import (
    stage as collect_directory,
)
from computer_use.interaction.read.acquisition.directory.passes.validate_directory_scope import (
    stage as validate_directory_scope,
)
from computer_use.native.query.directory import applications_query
from computer_use.pipeline import Pipeline, Scope, Sequence

if TYPE_CHECKING:
    from typing import AsyncIterator

    from computer_use.interaction.read.acquisition.directory.model import DirectoryRequest
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


class DirectoryCollectionPipeline(Pipeline["DirectoryPlan", "DirectoryFacts"]):
    """Collect one catalogue under an existing native operation.

    Validate that the lease has session-level directory scope, then execute the supplied
    native query. Return the application, surface, or root catalogue reported by the driver.

    Attributes:
        port: Native port used to issue the directory query under the active lease.
    """

    def __init__(self, port: NativePort) -> None:
        self.port = port
        super().__init__(root=self.build())

    def build(self) -> Step[DirectoryPlan, DirectoryFacts]:
        port = self.port

        return Sequence(
            name="DirectoryCollectionPipeline",
            steps=[validate_directory_scope(), collect_directory(port=port)],
        )


class DirectoryReadPipeline(Pipeline["DirectoryRequest", "DirectoryFacts"]):
    """Open a bounded operation for a native directory query.

    Create a session-level read lease using the request's time limit and run directory
    collection inside it. Return the catalogue after the native scope is released.

    Attributes:
        port: Native port and lifecycle service used to open and execute the directory read.
    """

    def __init__(self, port: NativePort) -> None:
        self.port = port
        super().__init__(root=self.build())

    def build(self) -> Step[DirectoryRequest, DirectoryFacts]:
        port = self.port

        @asynccontextmanager
        async def lease(request: DirectoryRequest, control: Control) -> AsyncIterator[DirectoryPlan]:
            async with port.calls.open(None, request.timeout_ms) as read:
                query = request.query
                if request.application_selector is not None:
                    query = applications_query(request.application_selector, capabilities=port.connection.capabilities)
                elif query["kind"] == "windowCatalogue" and (
                    port.connection.platform != "macos" or "nativeWindowCatalogue" not in port.connection.capabilities
                ):
                    raise local_failure(
                        "this backend does not provide a metadata-only visible window catalogue",
                        "unsupported_operation",
                    )
                elif query.get("includeEnded") and "nativeApplicationLifetime" not in port.connection.capabilities:
                    query = {"kind": "applications"}
                yield DirectoryPlan(read, query)

        return Scope(
            name="DirectoryReadScope",
            using=lease,
            body=NativeScopeBody(port, DirectoryCollectionPipeline(port), lambda value: value.read.remaining),
        )
