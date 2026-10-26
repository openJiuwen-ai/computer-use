"""Immutable fact identity with a separate semantic tree view."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree, SemanticNode
    from computer_use.protocol.observation import NativeObservation
    from computer_use.protocol.observation.adapters.records import DriverReadMetadata
    from computer_use.protocol.records import FactCollection, Record


@dataclass
class SemanticSnapshot:
    """Native fact batch with a semantic tree, display aliases, and captured field indexes.

    Attributes:
        native: Immutable unified graph, retaining values, native references, and read gaps.
        facts: Derived field/action/relation indexes used by common semantic rules.
        roots: Ordered roots in this observed or projected view.
        refs: Display element aliases mapped to native node tokens.
        annotations: Residual facts and incomplete-containment notes retained for the model.
        fields: Captured native fields indexed by display reference and field name.
        text_refs: Captured text sources keyed by display reference and semantic field path.
        scope_kind: Original read-scope kind, when specified.
        root_contexts: Per-root observation context; not an input authority or new session
            owner.
        value_fields: Primary native value field for each display reference and its
            continuation.
        driver_features: Capabilities advertised by the original native connection.
        collection: Native query scope and follow-up selectors retained outside the graph.
    """

    native: NativeObservation
    facts: FactCollection
    roots: list[SemanticNode]
    refs: dict[str, str]
    annotations: list[str]
    fields: dict[tuple[str, str], Record]
    text_refs: dict[tuple[str, str], Record]
    scope_kind: str | None = None
    root_contexts: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    # The same source must supply the primary text and its continuation.
    # Other native value interfaces remain in fields/native, never substituted.
    value_fields: dict[str, str] = field(default_factory=dict)
    driver_features: frozenset[str] = field(default_factory=frozenset)
    collection: DriverReadMetadata | None = None

    def render(self, *, trace: bool = False, compact: bool = False, public_api: bool = False) -> RenderedTree:
        from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
        from computer_use.interaction.read.presentation.pipeline import SnapshotPresentationPipeline

        return SnapshotPresentationPipeline().run(SnapshotRenderRequest(self, trace, compact, public_api))
