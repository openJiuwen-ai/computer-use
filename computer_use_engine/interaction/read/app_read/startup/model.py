# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.scopes import RunningApplication, WorkingWindow
    from computer_use.protocol.records import ApplicationInfo
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


PURE, QUERY, RESOURCE = {Effect.PURE}, {Effect.QUERY}, {Effect.RESOURCE}


@dataclass(frozen=True)
class StartupDirectoryState:
    """Startup identity and progress used by the bounded discovery loop.

    Attributes:
        application: Selected native application identity or catalogue entry.
        session: Driver session that owns the retained native references.
        launched: Whether this request dispatched a launch action.
        installation: Installation identity used to launch or rediscover the selected application.
        on_scope: Callback that retains the established running/working scope in its existing
            owner.
        attempt: Zero-based startup-discovery attempt consumed by the startup gate.
    """

    application: ApplicationInfo
    session: str
    launched: bool
    installation: str | None = None
    on_scope: Callable[[RunningApplication | WorkingWindow], None] | None = None
    attempt: int = 0


@dataclass(frozen=True)
class StartupLease:
    """Startup-discovery state within one native directory-query operation.

    Attributes:
        state: State retained by the enclosing read operation.
        read: Open native operation lease; owns the token and original millisecond deadline.
        applications: Application catalogue captured while resolving the requested scope.
        associated: Whether the running application was linked to the startup installation.
    """

    state: StartupDirectoryState
    read: OperationLease[Binding]
    applications: DirectoryFacts | None = None
    associated: bool = False


@dataclass(frozen=True)
class StartupAttempt:
    """Directory evidence and a possible stale-scope failure from one startup attempt.

    Attributes:
        state: State retained by the enclosing read operation.
        directory: Observed application/window catalogue, including incomplete relationships.
        stale: Scope-staleness failure observed during this startup attempt.
    """

    state: StartupDirectoryState
    directory: DirectoryFacts | None
    stale: ComputerUseError | None = None
