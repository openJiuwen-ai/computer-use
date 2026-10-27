"""Shared handle, observation, and result contracts for session execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, NotRequired, Protocol, TypeAlias, TypedDict

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.reader import CapturedField, ChildContinuation
    from computer_use.native.contracts import Binding, Intent, OperationTarget
    from computer_use.session.interface_images import CapturedWindow, InterfaceImage
    from computer_use.session.interfaces import InstanceState, WindowIdentity
    from computer_use.types import ActionResult, AppInfo, Observation, TextPage

Remaining: TypeAlias = Callable[[], int]
"""Return milliseconds left on the original call, or raise on expiration."""
ElementPath: TypeAlias = tuple[Mapping[str, Any], ...]
"""Normalized semantic lookup components, already parsed by the public API."""


class ElementSnapshot(TypedDict):
    """Retained observation data for one element reference.

    Attributes:
        binding: Native identity used to address the observed element.
        container: Whether the node is a layout/window container whose input can
            be forwarded to its current receiver.
        scope_kind: Native read scope, such as an application menu or window.
        source: Observation provenance, including its display reference and time.
        text: Captured text field used for paged reads.
        children: Captured child-relation continuation used for subtree reads.
        window_identity: Exact member-window lifetime that supplied the snapshot;
            separate from the native Binding, whose instance belongs to the App root.
    """

    binding: Binding
    container: bool
    scope_kind: str | None
    source: dict[str, Any]
    text: CapturedField | None
    children: ChildContinuation | None
    window_identity: NotRequired[WindowIdentity]


class UIHandle(Protocol):
    """App/Window state needed to translate public UI operations.

    Attributes:
        _instance: Shared instance membership, element references, and window views.
        _anchor: Fixed window key for a Window, or None for the App-wide view.
        _context: Most recently confirmed member window for unqualified input.
        _lookup_scopes: Additional observed owners used for application-menu lookup.
        _menu_only: Whether the current view contains only an application menu.
        _image: Captured image context retained for this UI view.
    """

    _instance: InstanceState
    _anchor: str | None
    _context: str | None
    _lookup_scopes: tuple[object, ...]
    _menu_only: bool
    _image: InterfaceImage | None


class WindowHandle(Protocol):
    """Link from a fixed Window view to its containing App handle."""

    @property
    def _app(self) -> UIHandle: ...


class ObservedWindow(Protocol):
    """Window identity and image evidence shared by observation and input.

    Attributes:
        target: Binding of the observed window or control.
        image: Captured window image and mapping, when available.
    """

    @property
    def target(self) -> Binding: ...
    @property
    def image(self) -> CapturedWindow | None: ...


WriteArguments: TypeAlias = (
    tuple["Intent", "OperationTarget", ElementPath | None, int]
    | tuple["Intent", "OperationTarget", ElementPath | None, int, bool]
)
"""Existing write entry tuples; the final flag requests current-target input."""

if TYPE_CHECKING:
    AgentResult: TypeAlias = Observation | ActionResult | TextPage | ElementSnapshot | list[AppInfo]
    QueryResult: TypeAlias = ElementSnapshot
    InteractionResult: TypeAlias = AgentResult | ActionResult | QueryResult
