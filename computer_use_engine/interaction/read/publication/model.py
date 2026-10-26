from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.document import DisplayDocument

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.images.overview import WindowOverview
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.protocol.records import Record
    from computer_use.session.state import AppState
    from computer_use.types import Observation


@dataclass(frozen=True)
class PublicationRequest:
    """Inputs for publishing an observation and updating its delivery state.

    Attributes:
        owner: Registered App, Window, or Element handle that owns the observation stream.
        state: Session-owned state holding this stream's delivered baseline and references.
        document: Captured structured observation awaiting final rendering.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        target: Native identity of the element, window, or application being observed.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        full: Whether delivery requests a complete tree instead of an eligible diff.
        view: Requested observation components: tree, image, or both.
        extra_trees: Additional target/snapshot pairs included with the primary observation.
        overview: Related-window preview and its source metadata, when collected.
    """

    owner: object
    state: AppState
    document: DisplayDocument | str
    tree: SemanticSnapshot | None
    target: Binding
    image: PNG | None = None
    full: bool = False
    view: str = "window"
    extra_trees: tuple[tuple[Binding, SemanticSnapshot], ...] = ()
    overview: WindowOverview | None = None


@dataclass(frozen=True)
class PublicationSources:
    """Exact source snapshots and prepared projections retained for delivery.

    Attributes:
        request: Observation delivery inputs; persistent bindings stay in the session owner.
        observations: Native targets paired with their source snapshots in publication order.
        rendered: Frozen source facts and compressed semantic projections.
    """

    request: PublicationRequest
    observations: tuple[tuple[Binding, SemanticSnapshot], ...]
    rendered: tuple[PreparedSnapshot, ...] = ()


@dataclass(frozen=True)
class PublicationText:
    """Structured observation and native-detail documents awaiting final rendering.

    Attributes:
        source: Source snapshots and their prepared projections.
        document: Structured model-facing sections, trees, and continuation instructions.
        details: Source-preserving native-detail document from the same capture.
    """

    source: PublicationSources
    document: DisplayDocument
    details: DisplayDocument


@dataclass(frozen=True)
class PublishedObservation:
    """Public observation paired with its single delivery event.

    Attributes:
        result: Public observation returned after publication.
        event: Observation event published alongside the public result.
    """

    result: Observation
    event: Record


if TYPE_CHECKING:
    from computer_use.protocol.png import PNG
