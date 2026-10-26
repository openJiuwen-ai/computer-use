"""Public UI intents translated into session reads, queries, and actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.records import ElementSnapshot, UIHandle
    from computer_use.protocol.records import Record
    from computer_use.types import ElementPath, Point


@dataclass(frozen=True)
class ObserveUI:
    """Observe an App instance or a fixed Window view.

    Attributes:
        owner: App/Window handle defining the observation scope.
        view: Requested tree, image, or combined observation.
        full: Request a complete tree rather than an eligible diff.
        menu_path: Menu hierarchy to inspect, or None for the current window.
    """

    owner: UIHandle
    view: str
    full: bool
    menu_path: Sequence[str] | None


@dataclass(frozen=True)
class ObserveDesktop:
    """Observe the desktop image or application directory.

    Attributes:
        view: Requested desktop image or application-directory view.
        full: Request a complete observation rather than an eligible diff.
    """

    view: str
    full: bool


@dataclass(frozen=True)
class ObserveElement:
    """Observe a bound element and its descendant structure.

    Attributes:
        owner: App/Window handle that owns the element reference.
        snapshot: Retained binding and child-continuation source for the element.
        cursor: Child-page continuation token, or None for the first page.
    """

    owner: UIHandle
    snapshot: ElementSnapshot
    cursor: str | None


@dataclass(frozen=True)
class ObserveText:
    """Read a page of an element's captured text.

    Attributes:
        owner: App/Window handle that owns the element reference.
        snapshot: Retained element binding and text source.
        cursor: Text-page continuation token, or None for the first page.
        length: Requested page length in the captured source's text units.
    """

    owner: UIHandle
    snapshot: ElementSnapshot
    cursor: str | None
    length: int


@dataclass(frozen=True)
class DiscoverApps:
    """Discover selectable application instances and publish their references."""

    pass


@dataclass(frozen=True)
class ResolveElement:
    """Locate an element within an App/Window scope.

    Attributes:
        owner: App/Window handle defining the lookup scope.
        target: Observed integer reference, exact name, or semantic path.
        timeout_ms: Lookup wait limit in milliseconds within the call budget.
    """

    owner: UIHandle
    target: int | str | ElementPath
    timeout_ms: int


@dataclass(frozen=True)
class ActOnElement:
    """Apply an operation to a previously observed element.

    Attributes:
        owner: App/Window handle that owns the element reference.
        snapshot: Element binding, provenance, and container classification.
        intent: Requested operation, such as click, insert, replace, or key.
        parameters: Parameters for that operation.
    """

    owner: UIHandle
    snapshot: ElementSnapshot
    intent: str
    parameters: Record


@dataclass(frozen=True)
class UIKeyboard:
    """Send keyboard input to an App/Window's current input context.

    Attributes:
        owner: App/Window handle constraining the receiving window.
        method: Public keyboard operation, such as typeText or pressKey.
        parameters: Text, key, or paste options for the operation.
    """

    owner: UIHandle
    method: str
    parameters: Record


@dataclass(frozen=True)
class UIPointer:
    """Apply a pointer operation in a delivered App/Window image.

    Attributes:
        owner: App/Window handle that owns the delivered image.
        intent: Pointer operation, such as click, drag, or scroll.
        points: Target/path points in delivered-image pixels.
        options: Button, modifiers, or other options for the pointer operation.
    """

    owner: UIHandle
    intent: str
    points: tuple[Point, ...]
    options: Record


READ_REQUESTS = (ObserveUI, ObserveDesktop, ObserveElement, ObserveText)
QUERY_REQUESTS = (DiscoverApps, ResolveElement)
WRITE_REQUESTS = (ActOnElement, UIKeyboard, UIPointer)
INTERFACE_REQUESTS = READ_REQUESTS + QUERY_REQUESTS + WRITE_REQUESTS

if TYPE_CHECKING:
    from typing import TypeAlias

    InterfaceRequest: TypeAlias = (
        ObserveUI
        | ObserveDesktop
        | ObserveElement
        | ObserveText
        | DiscoverApps
        | ResolveElement
        | ActOnElement
        | UIKeyboard
        | UIPointer
    )
