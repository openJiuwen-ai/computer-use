# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable fact identity with a separate semantic tree view."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree, SemanticNode
    from computer_use.protocol.observation import NativeObservation
    from computer_use.protocol.observation.adapters.records import DriverReadMetadata
    from computer_use.protocol.records import FactCollection, Record


class _FactIndex(Mapping):
    """An immutable index closed by the native-fact builder that owns its rows.

    This is an ownership certificate for that builder's complete graph, not a
    promise that arbitrary external mapping proxies have immutable children.
    """

    __slots__ = ("_data", "native")

    def __init__(self, native, data):
        object.__setattr__(self, "native", native)
        object.__setattr__(self, "_data", MappingProxyType(data))

    def __setattr__(self, name, value):
        raise AttributeError("captured fact indexes are immutable")

    def __delattr__(self, name):
        raise AttributeError("captured fact indexes are immutable")

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self


@dataclass(frozen=True)
class _CapturedSources:
    """Read-only source identities owned by one completed semantic projection."""

    native: NativeObservation
    facts: _FactIndex
    refs: Mapping
    annotations: tuple
    fields: Mapping
    text_refs: Mapping
    scope_kind: str | None
    root_contexts: Mapping
    value_fields: Mapping
    driver_features: frozenset

    def matches(self, snapshot):
        return all(getattr(snapshot, item.name) is getattr(self, item.name) for item in fields(self))


def _seal_projection_value(value):
    """Close a fresh projection record without touching its mutable display peer.

    Called only inside the projection producer after a _FactIndex has supplied
    its proven immutable values. Other containers here are newly derived
    provenance values (for example a row range) and must detach from roots.
    """
    if isinstance(value, MappingProxyType):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({key: _seal_projection_value(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_seal_projection_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_seal_projection_value(item) for item in value)
    return value


def _seal_projection_sources(snapshot):
    """Transfer only the private builder's proven source graph into a capsule."""
    if not isinstance(snapshot.facts, _FactIndex) or snapshot.facts.native is not snapshot.native:
        # Other SnapshotFacts implementations retain capture's normal detach
        # contract. Unknown ownership never authorizes skipping their copies.
        return snapshot
    snapshot.refs = MappingProxyType(snapshot.refs)
    snapshot.annotations = tuple(snapshot.annotations)
    snapshot.fields = MappingProxyType({key: _seal_projection_value(value) for key, value in snapshot.fields.items()})
    # These are fresh shallow copies of the fact builder's sealed statuses;
    # all descendants, including lazy complete issues, already belong to it.
    snapshot.text_refs = MappingProxyType({key: MappingProxyType(value) for key, value in snapshot.text_refs.items()})
    snapshot.root_contexts = MappingProxyType(snapshot.root_contexts)
    snapshot.value_fields = MappingProxyType(snapshot.value_fields)
    capsule = _CapturedSources(**{item.name: getattr(snapshot, item.name) for item in fields(_CapturedSources)})
    # The projection builder alone attaches this private ownership certificate.
    # pylint: disable-next=protected-access
    snapshot._captured_sources = capsule
    return snapshot


def _captured_sources(snapshot):
    """Return evidence only while the same immutable source fields are bound."""
    capsule = getattr(snapshot, "_captured_sources", None)
    return capsule if isinstance(capsule, _CapturedSources) and capsule.matches(snapshot) else None


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
