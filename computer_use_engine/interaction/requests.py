# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Requests passed between session adapters and the execution branches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .agent.requests import INTERFACE_REQUESTS

if TYPE_CHECKING:
    from collections.abc import Callable

    from computer_use.interaction.records import ElementPath as LookupPath
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.native.contracts import Intent, OperationTarget
    from computer_use.protocol.records import NativeDirectory
    from computer_use.session.interfaces import WindowIdentity


@dataclass(frozen=True)
class FindElement:
    """Resolve a semantic path inside a handle's observed query scope.

    Attributes:
        owner: Registered handle that owns the query root.
        path: Ordered name, role, or identifier conditions for the lookup.
        timeout_ms: Lookup wait limit in milliseconds, bounded by the enclosing call.
        enabled: Enabled-state flag supplied by the caller; current lookup
            resolves the path independently of this flag.
    """

    owner: object
    path: LookupPath
    timeout_ms: int
    enabled: bool
    strict_window: bool = False


@dataclass(frozen=True)
class ExecuteAction:
    """Execute an intent against a binding or a semantic lookup result.

    Attributes:
        owner: Registered App/window owner, or None for desktop input.
        intent: Operation and parameters, such as insertion, replacement, or a key.
        target: Binding or desktop image target to resolve before dispatch.
        path: Optional semantic path below the target's query root.
        query_timeout_ms: Lookup wait limit in milliseconds within the call budget.
        app_keyboard: Treat keyboard input as addressed to the App's current receiver.
        forward_current: Forward container input to its current receiver.
        snapshot: Original element provenance for write-time scope validation.
        display_window: Captured public image scope for receipt text only; never an input binding.
        on_pre_input_directory: Optional call-local observer of a validated directory.
    """

    owner: object | None
    intent: Intent
    target: OperationTarget
    path: LookupPath | None = None
    query_timeout_ms: int = 0
    app_keyboard: bool = False
    forward_current: bool = False
    snapshot: ElementSnapshot | None = None
    display_window: WindowIdentity | None = None
    on_pre_input_directory: Callable[[NativeDirectory], None] | None = None


@dataclass(frozen=True)
class ComputerInput:
    """Explicit global input, with optional points that need only geometric location."""

    intent: Intent
    points: tuple[object, ...] = ()


REQUEST_TYPES = INTERFACE_REQUESTS + (FindElement, ExecuteAction, ComputerInput)

if TYPE_CHECKING:
    from typing import TypeAlias

    from .agent.requests import InterfaceRequest

    InteractionRequest: TypeAlias = InterfaceRequest | FindElement | ExecuteAction | ComputerInput
