# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Data-only views consumed by presentation, independent of read services.

Semantic nodes are open dictionaries: platform metadata and unknown native
facts must survive without a closed list of allowed keys. Typed IR objects and
view/editor interfaces carry phase boundaries; these aliases describe the
existing heterogeneous tree/output dictionaries, without coercion or copying.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from computer_use.protocol.observation import NativeObservation
from computer_use.protocol.records import FactCollection, Record

if TYPE_CHECKING:
    from computer_use.protocol.observation.adapters.records import DriverReadMetadata


Reference: TypeAlias = str
"""Display alias; execution bindings are retained by their session owner."""
FormatField: TypeAlias = Callable[[str], str]
"""Pure formatter for one textual field."""
SemanticNode: TypeAlias = Record
"""Semantic source node with ref/role/children and open provider-derived fields.

Source facts, capabilities, control membership, and caption provenance are
retained. Compression writes through ProjectionEditor, never this source alias.
"""
RenderedTree: TypeAlias = Record
"""Existing rendering dictionary at a branch or assembled-snapshot boundary.

Branch renders contain ordered display refs and projected roots. Assembled
snapshot renders contain the display-to-native map, per-root projections, full
details, and field/text/relation accounting. Consumers keep that phase's shape;
this alias does not pretend the two forms have identical ``refs`` values.
Renderer results also retain a nonserialized ``document`` attribute containing
the immutable display structure used by the delivery diff.
"""
NodeView: TypeAlias = Mapping[str, Any]
"""Read-only source/projected node; arbitrary provider fields remain available."""
NodeForest: TypeAlias = Sequence[NodeView]
"""Ordered view roots; their native bindings are owned outside presentation."""


class SnapshotFacts(Protocol):
    """Facts required by presentation, with no driver or controller access.

    Attributes:
        native: Immutable raw object graph, never edited by presentation.
        facts: Derived semantic query indexes, captured independently of the graph.
        roots: Derived semantic roots with native bindings retained separately.
        refs: Display-reference to native-token map for this observation.
        annotations: Explicit residual facts and incomplete-containment notes.
        fields: Per-reference/native-field normalization/provenance records.
        text_refs: Immutable text continuations keyed by reference/native field.
        scope_kind: Original observation scope, when specified.
        root_contexts: Related-root owner/relation pairs indexed by display ref.
        value_fields: Primary native text source for each reference.
        driver_features: Capabilities advertised by the original connection.
    """

    native: NativeObservation
    facts: FactCollection
    roots: list[SemanticNode]
    refs: dict[str, str]
    annotations: list[str]
    fields: dict[tuple[str, str], Record]
    text_refs: dict[tuple[str, str], Record]
    scope_kind: str | None
    root_contexts: dict[str, tuple[tuple[str, str], ...]]
    value_fields: dict[str, str]
    driver_features: frozenset[str]
    collection: DriverReadMetadata | None


class ReferenceAllocator(Protocol):
    """The existing session owner that allocates display aliases.

    Attributes:
        session: Driver generation to which all allocated aliases belong.
    """

    session: str

    def reference(self, native: Hashable) -> str:
        """Return an alias for an exact native identity, without rebinding it.

        Args:
            native: Native object token or exact compound scope key in this session.

        Returns:
            The retained or newly allocated display reference.
        """
        ...
