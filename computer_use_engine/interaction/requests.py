"""Requests passed between session adapters and the execution branches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .agent.requests import INTERFACE_REQUESTS

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.records import ElementPath as LookupPath, ElementSnapshot
    from computer_use.native.contracts import Intent, OperationTarget
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class ReadApp:
    """Observe the selected application's current window or menu.

    Attributes:
        owner: Registered handle whose session state selects the read scope.
        full: Request a complete tree rather than an eligible diff.
        menu_path: Menu hierarchy to inspect, or None for the current window.
    """

    owner: object
    full: bool = False
    menu_path: Sequence[str] | None = None


@dataclass(frozen=True)
class ReadDesktop:
    """Observe the desktop image and its available application context.

    Attributes:
        full: Request a complete observation rather than an eligible diff.
    """

    full: bool = False


@dataclass(frozen=True)
class ReadElement:
    """Read a bound element or continue its captured child relation.

    Attributes:
        owner: Registered handle that owns the element observation.
        snapshot: Element binding, provenance, and retained continuation sources.
        query: Optional semantic lookup used to resolve the read target.
        offset: Start position in the child relation being read.
        previous: Earlier element snapshot used to continue the same observation.
        cursor: Opaque child-continuation token, or None for the first page.
    """

    owner: object
    snapshot: ElementSnapshot
    query: Record | None
    offset: int
    previous: ElementSnapshot | None = None
    cursor: str | None = None


@dataclass(frozen=True)
class ReadText:
    """Read text retained with an element observation.

    Attributes:
        owner: Registered handle that owns the captured text.
        snapshot: Element binding and captured text source.
        options: Text-page options, including continuation and requested length.
    """

    owner: object
    snapshot: ElementSnapshot
    options: Record


@dataclass(frozen=True)
class ListApplications:
    """Request the native application directory."""

    pass


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


@dataclass(frozen=True)
class ExecuteAction:
    """Execute an intent against a binding or a semantic lookup result.

    Attributes:
        owner: Registered App/Window owner, or None for desktop input.
        intent: Operation and parameters, such as insertion, replacement, or a key.
        target: Binding or desktop image target to resolve before dispatch.
        path: Optional semantic path below the target's query root.
        query_timeout_ms: Lookup wait limit in milliseconds within the call budget.
        app_keyboard: Treat keyboard input as addressed to the App's current receiver.
        forward_current: Forward container input to its current receiver.
    """

    owner: object | None
    intent: Intent
    target: OperationTarget
    path: LookupPath | None = None
    query_timeout_ms: int = 0
    app_keyboard: bool = False
    forward_current: bool = False


REQUEST_TYPES = INTERFACE_REQUESTS + (
    ReadApp,
    ReadDesktop,
    ReadElement,
    ReadText,
    ListApplications,
    FindElement,
    ExecuteAction,
)

if TYPE_CHECKING:
    from typing import TypeAlias

    from .agent.requests import InterfaceRequest

    InteractionRequest: TypeAlias = (
        InterfaceRequest
        | ReadApp
        | ReadDesktop
        | ReadElement
        | ReadText
        | ListApplications
        | FindElement
        | ExecuteAction
    )
