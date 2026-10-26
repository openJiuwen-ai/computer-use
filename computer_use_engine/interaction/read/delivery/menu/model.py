from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.acquisition.menus.query import MenuRead
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.scopes import WorkingWindow
    from computer_use.protocol.records import ApplicationInfo, NativeDirectory as DirectoryFacts, Record


PURE, QUERY, RESOURCE = {Effect.PURE}, {Effect.QUERY}, {Effect.RESOURCE}


@dataclass(frozen=True)
class MenuSourceRenderRequest:
    """Menu snapshots and formatting options for rendering their source text.

    Attributes:
        menu: Collected native menu sources and their query scope.
        compact: Whether to use compact task-facing text while retaining full source details.
    """

    menu: MenuRead
    compact: bool = False


@dataclass(frozen=True)
class RenderedMenuSources:
    """Prepared documents for menu sources in their observation order.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        documents: Structured menu parts in the same order as the request's sources.
    """

    request: MenuSourceRenderRequest
    documents: tuple[DisplayDocument, ...]


@dataclass(frozen=True)
class MenuObservationRequest:
    """Application context, menu path, and collection limits for a menu read.

    Attributes:
        application: Selected native application identity or catalogue entry.
        selection: Fixed working-instance/root-window selection, when established.
        directory: Observed application/window catalogue, including incomplete relationships.
        path: Ordered semantic lookup/menu components within the declared target scope.
        launch: Original startup receipt; retained even when later observation fails.
        limits: Requested native collection, image, and view limits.
    """

    application: ApplicationInfo
    selection: WorkingWindow | None
    directory: DirectoryFacts | None
    path: Sequence[str]
    launch: Record | None
    limits: Record


@dataclass(frozen=True)
class MenuObservationPlan:
    """Menu request paired with the native context selected for collection.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        context: Native window or application context used to resolve menu sources.
    """

    request: MenuObservationRequest
    context: Binding | None


@dataclass(frozen=True)
class CollectedMenuObservation:
    """Collected menu snapshots and the latest observed application directory.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        menu: Collected native menu sources and their query scope.
        directory: Observed application/window catalogue, including incomplete relationships.
    """

    request: MenuObservationRequest
    menu: MenuRead
    directory: DirectoryFacts | None


@dataclass(frozen=True)
class MenuDirectoryLease:
    """Collected menu evidence within a directory-recheck operation.

    Attributes:
        observed: Capture provenance or original observation used by the current stage.
        read: Open native operation lease; owns the token and original millisecond deadline.
    """

    observed: CollectedMenuObservation
    read: OperationLease[Binding]


@dataclass(frozen=True)
class MenuTextDraft:
    """Menu evidence, window aliases, and structured observation sections.

    Attributes:
        observed: Collected menus and the rechecked application directory.
        windows: Display window aliases mapped to native surface identities.
        lines: Deferred header fields and prepared menu documents accumulated for delivery.
    """

    observed: CollectedMenuObservation
    windows: dict[str, str]
    lines: tuple[str | DisplayText | DisplayDocument, ...] = ()
