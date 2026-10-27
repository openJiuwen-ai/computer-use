"""Immutable display projections prepared before delivery and text rendering."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from computer_use.interaction.read.presentation.fallback import display_node_facts
from computer_use.interaction.read.presentation.ir import ProjectionInput, ProjectionResult, freeze, thaw
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import PresentedValue
    from computer_use.interaction.read.presentation.records import RenderedTree, SnapshotFacts
    from computer_use.pipeline.core import Control
    from computer_use.protocol.observation import NativeObservation
    from computer_use.protocol.observation.adapters.records import DriverReadMetadata
    from computer_use.protocol.records import FactCollection, Record


@dataclass
class _MaterializedSnapshot:
    """Request-local fact collections used by pure preparation and final formatting."""

    native: NativeObservation
    facts: FactCollection
    roots: list[Record]
    refs: dict[str, str]
    annotations: list[str]
    fields: dict[tuple[str, str], Record]
    text_refs: dict[tuple[str, str], Record]
    scope_kind: str | None
    root_contexts: dict[str, tuple[tuple[str, str], ...]]
    value_fields: dict[str, str]
    driver_features: frozenset[str]
    collection: DriverReadMetadata | None = None


def _copy_collections(value: Any, *, immutable: bool) -> Any:
    """Copy nested fact collections without consuming Python recursion depth."""
    containers = (Mapping, tuple, list, set, frozenset)
    concrete_containers = (dict, MappingProxyType, tuple, list, set, frozenset)
    atomic = (str, int, float, bool, type(None), bytes)

    def is_container(item: Any) -> bool:
        # Native fact batches contain many scalar property/status values.
        # Avoid an ABC Mapping lookup for every scalar on every frozen copy;
        # retain the generic check for custom mappings/container subclasses.
        kind = type(item)
        return kind in concrete_containers or kind not in atomic and isinstance(item, containers)

    def is_mapping(item: Any) -> bool:
        kind = type(item)
        return kind in (dict, MappingProxyType) or kind not in concrete_containers and isinstance(item, Mapping)

    completed: dict[int, Any] = {}
    active: set[int] = set()
    pending = [(value, False)]
    while pending:
        current, finish = pending.pop()
        if not is_container(current) or id(current) in completed:
            continue
        if not finish:
            if id(current) in active:
                raise ValueError("Captured presentation facts contain a collection cycle")
            active.add(id(current))
            pending.append((current, True))
            children = current.values() if is_mapping(current) else current
            pending.extend((child, False) for child in reversed(tuple(children)))
            continue

        def copied(item: Any) -> Any:
            return completed[id(item)] if is_container(item) else item

        if is_mapping(current):
            result = {key: copied(item) for key, item in current.items()}
            completed[id(current)] = MappingProxyType(result) if immutable else result
        elif isinstance(current, (set, frozenset)):
            result = (copied(item) for item in current)
            completed[id(current)] = frozenset(result) if immutable else set(result)
        else:
            result = [copied(item) for item in current]
            completed[id(current)] = tuple(result) if immutable else result
        active.remove(id(current))
    return completed.get(id(value), value)


@dataclass(frozen=True)
class CapturedSnapshot:
    """Freeze the captured facts and aliases used to prepare one display.

    Every collection is detached from its originating snapshot. Rendering creates a
    request-local copy so neither later observations nor callers can change this view.
    """

    native: NativeObservation
    facts: Mapping[str, Any]
    roots: tuple[Mapping[str, Any], ...]
    refs: Mapping[str, str]
    annotations: tuple[str, ...]
    fields: Mapping[tuple[str, str], Mapping[str, Any]]
    text_refs: Mapping[tuple[str, str], Mapping[str, Any]]
    scope_kind: str | None
    root_contexts: Mapping[str, tuple[tuple[str, str], ...]]
    value_fields: Mapping[str, str]
    driver_features: frozenset[str]
    collection: DriverReadMetadata | None = None

    @classmethod
    def capture(cls, snapshot: SnapshotFacts) -> CapturedSnapshot:
        """Copy and freeze the facts needed by compression and source accounting."""
        return cls(
            **{
                item.name: _copy_collections(
                    getattr(snapshot, item.name, None) if item.name == "collection" else getattr(snapshot, item.name),
                    immutable=True,
                )
                for item in fields(cls)
            }
        )

    def materialize(self) -> SnapshotFacts:
        """Return a local semantic snapshot for the existing pure presentation routines."""
        values = {item.name: _copy_collections(getattr(self, item.name), immutable=False) for item in fields(self)}
        values["driver_features"] = self.driver_features
        return _MaterializedSnapshot(**values)


@dataclass(frozen=True)
class PreparationFailure:
    """Captured presentation failure retained for source-preserving final rendering."""

    kind: type[Exception]
    arguments: tuple[Any, ...]
    detail: str

    @classmethod
    def capture(cls, error: Exception) -> PreparationFailure:
        return cls(type(error), freeze(error.args), str(error))

    def exception(self) -> Exception:
        return self.kind(*thaw(self.arguments))


@dataclass(frozen=True)
class PreparedNode:
    """One projected display node before labels, fields, or lines are formatted.

    Attributes:
        ref: Display reference with its original native binding retained by the snapshot.
        parent: Projected parent reference, or None for an independent root.
        depth: Projected traversal depth before reversible line packing.
        fields: Immutable semantic fields; child ordering and native facts stay separate.
    """

    ref: str
    parent: str | None
    depth: int
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class PreparedRoot:
    """A compressed root and its immutable source/provenance data.

    Attributes:
        root_ref: Original root used to derive this projection.
        members: Captured source membership, including folded and unexpanded references.
        projection: Frozen compressed forest, or None when source-fact fallback is needed.
        source: Immutable source fields and original containment for provenance checks.
        provenance: Source facts contributing to composed display values.
        failure: Compression failure to describe when the final output is rendered.
    """

    root_ref: str
    members: tuple[str, ...]
    projection: Mapping[str, Any] | None
    source: ProjectionInput | None = None
    provenance: Mapping[tuple[str, str], PresentedValue] | None = None
    failure: PreparationFailure | None = None

    @classmethod
    def capture(cls, root_ref: str, members: tuple[str, ...], projection: ProjectionResult) -> PreparedRoot:
        return cls(
            root_ref, members, _copy_collections(projection, immutable=True), projection.source, projection.provenance
        )

    def materialize(self) -> ProjectionResult:
        """Copy the retained projection without repeating semantic or compression passes."""
        if self.projection is None or self.source is None:
            raise ValueError("An uncompressed branch has no display projection")
        return ProjectionResult(
            _copy_collections(self.projection, immutable=False), self.source, dict(self.provenance or {})
        )


@dataclass(frozen=True)
class PreparedSnapshot:
    """Source-preserving structured presentation ready for comparison or final rendering.

    Attributes:
        request: Display options and a detached immutable copy of the acquired facts.
        roots: Independently compressed roots in snapshot order.
        nodes: Ordered projected nodes with fields and explicit containment, before text.
    """

    request: SnapshotRenderRequest
    roots: tuple[PreparedRoot, ...]
    nodes: tuple[PreparedNode, ...]

    @property
    def snapshot(self) -> CapturedSnapshot:
        return self.request.snapshot

    @property
    def refs(self) -> tuple[str, ...]:
        """Return visible references in the exact order used for display numbering."""
        return tuple(node.ref for node in self.nodes)

    @property
    def facts(self) -> Mapping[str, Any]:
        return self.snapshot.facts

    @property
    def inline_child_refs(self) -> tuple[str, ...]:
        """Return references whose child continuation is represented in the tree body."""
        from computer_use.interaction.read.presentation.compact import unexpanded_menu

        if not self.request.compact:
            return ()
        refs = []

        def _iter_root(_source_items: Iterator[Any]) -> Iterator[Any]:
            for root in _source_items:
                if root.projection is not None:
                    for ref, node in root.projection["nativeNodes"].items():
                        yield (ref, node)

        native_nodes = dict(_iter_root(iter(self.roots)))
        projected_nodes = {}

        def _iter_root_items(_source_items: Iterator[Any]) -> Iterator[Any]:
            for root in _source_items:
                if root.projection is not None:
                    for node in root.projection["roots"]:
                        yield node

        pending = list(_iter_root_items(iter(self.roots)))
        while pending:
            projected = pending.pop()
            projected_nodes[projected["ref"]] = projected
            pending.extend(projected["children"])
        for node in self.nodes:
            facts = node.fields
            if "role" not in facts:
                continue
            children = native_nodes.get(node.ref, {}).get("children", ())
            partial = facts.get("partialReason") is not None or facts.get("childrenRead", len(children)) < facts.get(
                "childCount", 0
            )
            projected = projected_nodes.get(node.ref)
            matches_partial = (
                True
                if (projected is not None and unexpanded_menu(projected))
                or (partial and "childrenStart" in facts and "nextChildOffset" in facts)
                else False
            )
            if matches_partial:
                refs.append(node.ref)
        return tuple(sorted(refs))


def prepared_nodes(roots: tuple[PreparedRoot, ...], snapshot: CapturedSnapshot) -> tuple[PreparedNode, ...]:
    """Index display containment directly from prepared projections and fallback records."""
    result: list[PreparedNode] = []
    native = {node["node"]: node for node in snapshot.facts["nodes"]}

    def visit(node: Mapping[str, Any], parent: str | None, depth: int) -> None:
        result.append(
            PreparedNode(
                node["ref"],
                parent,
                depth,
                freeze({key: value for key, value in node.items() if key not in ("children", "native")}),
            )
        )
        for child in node["children"]:
            visit(child, node["ref"], depth + 1)

    for root in roots:
        if root.projection is None:
            for ref in root.members:
                facts = display_node_facts(native[snapshot.refs[ref]])
                result.append(PreparedNode(ref, None, 0, freeze(facts)))
        else:
            for node in root.projection["roots"]:
                visit(node, None, 0)
    return tuple(result)


def prepare_snapshot(
    snapshot: SnapshotFacts,
    *,
    compact: bool = True,
    public_api: bool = True,
    trace: bool = False,
    _control: Control | None = None,
) -> PreparedSnapshot:
    """Prepare frozen display projections without generating or parsing presentation text."""
    from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline

    return PrepareSnapshotPipeline().run(SnapshotRenderRequest(snapshot, trace, compact, public_api), _control)


def render_prepared_snapshot(prepared: PreparedSnapshot, *, _control: Control | None = None) -> RenderedTree:
    """Render a retained presentation without reacquiring facts or recompressing its trees."""
    from computer_use.interaction.read.presentation.pipeline import RenderPreparedSnapshotPipeline

    return RenderPreparedSnapshotPipeline().run(prepared, _control)
