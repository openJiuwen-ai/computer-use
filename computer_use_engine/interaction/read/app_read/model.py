from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.app import WorkingWindow
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from typing import Callable, Sequence

    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.scopes import RunningApplication
    from computer_use.protocol.records import ApplicationInfo
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


PURE, QUERY, RESOURCE = {Effect.PURE}, {Effect.QUERY}, {Effect.RESOURCE}


@dataclass(frozen=True)
class ReadRequest:
    """Application read options used to select a working scope and collect its interface.

    Attributes:
        selector: Application name, running application identity, or established working window.
        window: Optional native window selector within the selected application.
        fields: Native fields requested from each collected element.
        max_nodes: Maximum number of native objects this collection may read.
        max_depth: Maximum native relation depth; None uses the existing platform default.
        max_dimension: Maximum image edge length in pixels, preserving aspect ratio.
        timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
        allow_launch: Whether this request permits explicit startup when no running scope is selected.
        menu_path: Requested menu path; None keeps the ordinary window read.
        on_scope: Callback that retains the established running/working scope in its existing
            owner.
        view: Requested observation components: tree, image, or both.
        overview: Whether to collect previews of related application windows.
    """

    selector: str | RunningApplication | WorkingWindow
    window: str | None = None
    fields: list[str] | None = None
    max_nodes: int = 1000
    max_depth: int | None = None
    max_dimension: int = 2048
    timeout_ms: int = 30000
    allow_launch: bool = True
    menu_path: Sequence[str] | None = None
    on_scope: Callable[[RunningApplication | WorkingWindow], None] | None = None
    view: str = "both"
    overview: bool = True


@dataclass
class _StartupReceipt:
    """Mutable launch receipt shared across the stages of one application read.

    Attributes:
        launch: Original startup receipt; retained even when later observation fails.
    """

    launch: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ReadInvocation:
    """Parsed read request paired with its launch-receipt owner.

    Attributes:
        request: Application observation options before working-scope selection.
        receipt: Shared owner of the launch receipt for this read.
    """

    request: ReadRequest
    receipt: _StartupReceipt


@dataclass(frozen=True)
class ReadCall:
    """One application read sharing the original call budget and launch receipt.

    Attributes:
        request: Application observation options before working-scope selection.
        remaining: Callback returning milliseconds left on the original call; may raise on
            expiration.
        receipt: Shared owner of the launch receipt for this read.
    """

    request: ReadRequest
    remaining: Remaining
    receipt: _StartupReceipt


@dataclass(frozen=True)
class ApplicationSelection:
    """Selected application and directory evidence used to establish a working scope.

    Attributes:
        call: Application read request with its shared deadline and startup receipt.
        application: Selected native application identity or catalogue entry.
        directory: Observed application/window catalogue, including incomplete relationships.
        applications: Application catalogue captured while resolving the requested scope.
        selection: Fixed working-instance/root-window selection, when established.
        installation: Installation identity used to launch or rediscover the selected application.
    """

    call: ReadCall
    application: ApplicationInfo
    directory: DirectoryFacts | None
    applications: DirectoryFacts | None = None
    selection: WorkingWindow | None = None
    installation: str | None = None

    @property
    def request(self) -> ReadRequest:
        return self.call.request

    @property
    def launch(self) -> Mapping[str, Any] | None:
        return self.call.receipt.launch


@dataclass(frozen=True)
class DirectoryLease:
    """Application-selection inputs within an open directory-query operation.

    Attributes:
        call: Application read request with its shared deadline and startup receipt.
        read: Open native operation lease; owns the token and original millisecond deadline.
        application: Selected native application identity or catalogue entry.
        applications: Application catalogue captured while resolving the requested scope.
    """

    call: ReadCall
    read: OperationLease[Binding]
    application: ApplicationInfo | None = None
    applications: DirectoryFacts | None = None


if TYPE_CHECKING:
    from typing import Any, Mapping
