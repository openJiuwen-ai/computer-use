from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.query import MenuObservation
from computer_use.native.contracts import Binding
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.acquisition.menus.query import MenuQuery, MenuSources
    from computer_use.native.lifecycle import OperationLease
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


PURE, QUERY = {Effect.PURE}, {Effect.QUERY}


@dataclass(frozen=True)
class MenuPathRequest:
    """Native menu-query scope and the ordered labels to resolve.

    Attributes:
        query: Native directory query specifying its catalogue kind and scope.
        path: Ordered semantic lookup/menu components within the declared target scope.
    """

    query: MenuQuery
    path: Sequence[str]


@dataclass(frozen=True)
class MenuLevel:
    """Current menu parent and the path component being resolved beneath it.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        current: Latest observed directory or scope after the collection step.
        ancestors: Native menu identities traversed before the current item.
        index: Zero-based index of the current component in the requested menu path.
        title: Requested label for the current menu path component.
    """

    request: MenuPathRequest
    current: str
    ancestors: tuple[str, ...]
    index: int
    title: str


@dataclass(frozen=True)
class MenuLevelFacts:
    """Menu-level context and its observed child identities and labels.

    Attributes:
        level: Parent-menu identity and path component being resolved.
        names: Child menu identities paired with their observed labels.
    """

    level: MenuLevel
    names: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MatchedMenuItem:
    """Resolved path component and the ancestor chain leading to it.

    Attributes:
        level: Parent-menu identity and path component being resolved.
        selected: Native menu items selected by resolving the requested path.
        ancestors: Native menu identities traversed before the current item.
    """

    level: MenuLevel
    selected: str
    ancestors: tuple[str, ...]


@dataclass(frozen=True)
class MenuItemFacts:
    """Matched menu item with its role and child-role evidence.

    Attributes:
        matched: Matched menu item and the path evidence that selected it.
        role: Observed or normalized role; role alone does not prove an action succeeds.
        children: Native identities of the matched item's children.
        roles: Child identities paired with their observed native roles.
    """

    matched: MatchedMenuItem
    role: str
    children: tuple[str, ...] = ()
    roles: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MenuCollectionRequest:
    """Application or window menu scope, optional path, and native read limits.

    Attributes:
        application: Native application identity whose menus are being read.
        context: Native window or application context used to resolve menu sources.
        path: Ordered semantic lookup/menu components within the declared target scope.
        summary: Whether to collect a bounded menu summary instead of the deeper requested subtree.
        source_directory: Catalogue captured with the source facts, before scope rechecking.
        fields: Native fields requested for each collected menu element.
        max_nodes: Maximum number of native objects this collection may read.
        max_depth: Maximum native relation depth; None uses the existing platform default.
    """

    application: str
    context: Binding | None = None
    path: Sequence[str] = ()
    summary: bool = False
    source_directory: DirectoryFacts | None = None
    fields: list[str] | None = None
    max_nodes: int = 1000
    max_depth: int | None = 40


@dataclass(frozen=True)
class MenuLease:
    """Menu collection request within an open native operation.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        read: Open native operation lease; owns the token and original millisecond deadline.
    """

    request: MenuCollectionRequest
    read: OperationLease[Binding]


@dataclass(frozen=True)
class DiscoveredWindowMenus:
    """Native menu operation paired with the discovered window menu roots.

    Attributes:
        lease: Stage input with the existing native lease and call budget.
        sources: Original sources or explicit source destinations retained by this stage.
    """

    lease: MenuLease
    sources: MenuSources


@dataclass(frozen=True)
class SelectedWindowMenus:
    """Discovered menu roots and the path targets selected within them.

    Attributes:
        discovered: Window-menu discovery result before path selection.
        selected: Native menu items selected by resolving the requested path.
    """

    discovered: DiscoveredWindowMenus
    selected: tuple[Binding, ...]


@dataclass(frozen=True)
class CollectedMenuRoots:
    """Menu request paired with the application's native root catalogue.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        roots: Catalogue containing the application's native accessibility roots.
    """

    request: MenuCollectionRequest
    roots: DirectoryFacts


@dataclass(frozen=True)
class ApplicationMenuTarget:
    """Application menu root selected from the collected native catalogue.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        roots: Root catalogue from which the application menu was selected.
        target: Native identity of the element, window, or application being observed.
    """

    request: MenuCollectionRequest
    roots: DirectoryFacts
    target: Binding


@dataclass(frozen=True)
class MenuFactPart:
    """One native menu source with its scope label and collection budget.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        max_nodes: Maximum number of native objects this collection may read.
        directory: Observed application/window catalogue, including incomplete relationships.
        scope_kind: Original read-scope kind, when specified.
    """

    target: Binding
    max_nodes: int
    directory: DirectoryFacts | None
    scope_kind: str


@dataclass(frozen=True)
class MenuPartsPlan:
    """Native menu operation and the source snapshots scheduled for collection.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        read: Open native operation lease; owns the token and original millisecond deadline.
        parts: Native menu sources and per-source budgets scheduled for collection.
        target: Native identity of the element, window, or application being observed.
        discovery_gaps: Incomplete menu-discovery notes carried into the final menu observation.
    """

    request: MenuCollectionRequest
    read: OperationLease[Binding]
    parts: tuple[MenuFactPart, ...]
    target: Binding
    discovery_gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectedMenuParts:
    """Menu collection plan paired with the snapshots it produced.

    Attributes:
        plan: Resolved stage plan; its fields and limits come from the original request.
        parts: Source observations collected in the plan's order.
    """

    plan: MenuPartsPlan
    parts: tuple[MenuObservation, ...]
