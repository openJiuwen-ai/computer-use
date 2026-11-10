# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable display projections prepared before delivery and text rendering."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from computer_use.interaction.read.presentation.fallback import display_node_facts
from computer_use.interaction.read.presentation.interface.disclosure.groups.model import BackgroundOverview
from computer_use.interaction.read.presentation.interface.signatures import interface_layout_signatures
from computer_use.interaction.read.presentation.ir import ProjectionInput, ProjectionResult, freeze, thaw
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.selection_summary import selection_summary_plan

if TYPE_CHECKING:
    from collections.abc import Callable

    from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
    from computer_use.interaction.read.presentation.interface.collections.model import CollectionDocument
    from computer_use.interaction.read.presentation.interface.composition.content.model import FieldContentPlan
    from computer_use.interaction.read.presentation.interface.coverage.model import CoveragePlan
    from computer_use.interaction.read.presentation.interface.disclosure.groups.model import GroupDisclosurePlan
    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.interface.readable.model import ReadablePlan
    from computer_use.interaction.read.presentation.interface.sharing.model import SharingPlan
    from computer_use.interaction.read.presentation.interface.sharing.text_slices.model import TextSliceDocument
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

    @cached_property
    def presentation_nodes(self):
        return {node["node"]: node for node in self.facts["nodes"]}

    @cached_property
    def presentation_relations(self):
        from .accounting.relations import RelationPresentation

        return RelationPresentation(self.facts, self.refs)


def _copy_collections(value: Any, *, immutable: bool, checkpoint: Callable[[], object] | None = None) -> Any:
    """Detach nested fact collections while retaining their shared-child identity.

    Iterative traversal supports deep captured trees and rejects collection cycles.
    Container categories are cached per concrete type; atomic values, including
    immutable native records, retain their identity and skip the traversal stack.

    Args:
        value: Captured facts or a nested collection to copy. Mapping keys stay intact.
        immutable: Produce mapping proxies, tuples, and frozen sets when true;
            otherwise produce dictionaries, lists, and sets.
        checkpoint: Original call-budget check, consulted within bounded work batches.

    Returns:
        A detached collection graph, or the original value when it is atomic.

    Raises:
        ValueError: A collection contains itself through its descendants.
    """
    if checkpoint is not None:
        checkpoint()
    work = 0

    def progress() -> None:
        nonlocal work
        work += 1
        if checkpoint is not None and work % 128 == 0:
            checkpoint()

    mapping, sequence, setlike = 1, 2, 3
    categories: dict[type, int] = {
        dict: mapping,
        MappingProxyType: mapping,
        tuple: sequence,
        list: sequence,
        set: setlike,
        frozenset: setlike,
    }

    def category(item: Any) -> int:
        """Classify each concrete type once, retaining Mapping subclass support."""
        concrete_type = type(item)
        selected = categories.get(concrete_type)
        if selected is None:
            selected = (
                mapping
                if isinstance(item, Mapping)
                else sequence
                if isinstance(item, (tuple, list))
                else setlike
                if isinstance(item, (set, frozenset))
                else 0
            )
            categories[concrete_type] = selected
        return selected

    if not category(value):
        return value
    completed: dict[int, Any] = {}
    active: set[int] = set()
    pending = [(value, category(value), None, None)]
    while pending:
        progress()
        current, kind, local, children = pending.pop()
        identity = id(current)
        if identity in completed:
            continue
        if local is None:
            if identity in active:
                raise ValueError("Captured presentation facts contain a collection cycle")
            active.add(identity)
            local = {} if kind == mapping else []
            items = current.items() if kind == mapping else enumerate(current)
            children = []
            # Scan even a single large container in bounded batches. Atomic
            # fields retain their identity and never enter the traversal stack.
            for key, child in items:
                progress()
                if kind == mapping:
                    local[key] = child
                else:
                    local.append(child)
                child_kind = category(child)
                if child_kind:
                    children.append((key, child, child_kind))
            pending.append((current, kind, local, children))
            for _, child, child_kind in reversed(children):
                progress()
                pending.append((child, child_kind, None, None))
            continue
        for key, child, _ in children:
            progress()
            local[key] = completed[id(child)]
        if kind == mapping:
            completed[identity] = MappingProxyType(local) if immutable else local
        elif kind == setlike:
            completed[identity] = frozenset(local) if immutable else set(local)
        else:
            completed[identity] = tuple(local) if immutable else local
        active.remove(identity)
    return completed[id(value)]


@dataclass(frozen=True)
class CapturedSnapshot:
    """Freeze the captured facts and aliases used to prepare one display.

    Immutable source graphs closed by their producer are shared, and unproven
    collections are detached. Rendering creates local display copies, retaining
    this capture independently of later observations and caller edits.
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

    @cached_property
    def geometric_index(self):
        """Capture-wide immutable evidence; each layout keeps its own scope."""
        from .interface.geometric.source import GeometricSourceIndex

        return GeometricSourceIndex.capture(self)

    @classmethod
    def capture(cls, snapshot: SnapshotFacts, *, checkpoint: Callable[[], object] | None = None) -> CapturedSnapshot:
        """Retain proven immutable sources and detach the editable display forest."""
        from computer_use.interaction.read.facts.snapshot import _captured_sources

        sources = _captured_sources(snapshot)
        if sources is not None:
            if checkpoint is not None:
                checkpoint()
            values = {item.name: getattr(sources, item.name) for item in dataclass_fields(sources)}
            values["roots"] = _copy_collections(snapshot.roots, immutable=True, checkpoint=checkpoint)
            values["collection"] = getattr(snapshot, "collection", None)
            return cls(**values)
        values = {
            item.name: getattr(snapshot, item.name, None) if item.name == "collection" else getattr(snapshot, item.name)
            for item in dataclass_fields(cls)
        }
        # One memo covers aliases shared by the fact, field and tree indexes.
        return cls(**_copy_collections(values, immutable=True, checkpoint=checkpoint))

    def materialize(
        self, *, read_only_sources: bool = False, checkpoint: Callable[[], object] | None = None
    ) -> SnapshotFacts:
        """Copy the editable display forest, sharing proven immutable sources.

        The default remains a detached editable snapshot. Internal pure display
        passes only edit roots: aliases, provenance, fields and native facts all
        remain capture-owned read-only inputs rather than being recopied for
        every preparation and rendering pass.
        """
        values = {
            item.name: (
                getattr(self, item.name)
                if read_only_sources and item.name != "roots"
                else _copy_collections(getattr(self, item.name), immutable=False, checkpoint=checkpoint)
            )
            for item in dataclass_fields(self)
        }
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
        interface: Source-bound reading blocks selected before delivery and text formatting.
        disclosure: Exact passive fields whose full captured text is available in details.
        atoms: Typed source-bound fields prepared before layout and terminal serialization.
        readable: Source-bound field typography and numbered same-result native notes.
    """

    root_ref: str
    members: tuple[str, ...]
    projection: Mapping[str, Any] | None
    source: ProjectionInput | None = None
    provenance: Mapping[tuple[str, str], PresentedValue] | None = None
    failure: PreparationFailure | None = None
    interface: InterfaceDocument | None = None
    disclosure: DisclosurePlan | None = None
    atoms: AtomDocument | None = None
    collections: CollectionDocument | None = None
    fields: FieldContentPlan | None = None
    sharing: SharingPlan | None = None
    coverage: CoveragePlan | None = None
    text_slices: TextSliceDocument | None = None
    groups: GroupDisclosurePlan | None = None
    readable: ReadablePlan | None = None

    @classmethod
    def capture(
        cls,
        root_ref: str,
        members: tuple[str, ...],
        projection: ProjectionResult,
        *,
        interface: InterfaceDocument | None = None,
        disclosure: DisclosurePlan | None = None,
        atoms: AtomDocument | None = None,
        collections: CollectionDocument | None = None,
        fields: FieldContentPlan | None = None,
        sharing: SharingPlan | None = None,
        coverage: CoveragePlan | None = None,
        text_slices: TextSliceDocument | None = None,
        groups: GroupDisclosurePlan | None = None,
        readable: ReadablePlan | None = None,
        checkpoint: Callable[[], object] | None = None,
    ) -> PreparedRoot:
        return cls(
            root_ref,
            members,
            _copy_collections(projection, immutable=True, checkpoint=checkpoint),
            projection.source,
            projection.provenance,
            interface=interface,
            disclosure=disclosure,
            atoms=atoms,
            collections=collections,
            fields=fields,
            sharing=sharing,
            coverage=coverage,
            text_slices=text_slices,
            groups=groups,
            readable=readable,
        )

    def materialize(self, *, checkpoint: Callable[[], object] | None = None) -> ProjectionResult:
        """Copy the retained projection without repeating semantic or compression passes."""
        if self.projection is None or self.source is None:
            raise ValueError("An uncompressed branch has no display projection")
        return ProjectionResult(
            _copy_collections(self.projection, immutable=False, checkpoint=checkpoint),
            self.source,
            _copy_collections(self.provenance or {}, immutable=False, checkpoint=checkpoint),
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


def prepared_nodes(
    roots: tuple[PreparedRoot, ...],
    snapshot: CapturedSnapshot,
    *,
    defer_inactive_selections: bool = False,
    checkpoint: Callable[[], object] | None = None,
) -> tuple[PreparedNode, ...]:
    """Index the fields and containment used to compare the final display.

    Args:
        roots: Frozen display projections, original atoms and source coverage.
        snapshot: Unchanged captured facts and their original reference bindings.
        defer_inactive_selections: Match the default compact delivery policy. A
            complete unfocused collapsed range may be excluded from comparison
            only when its exact atom claim is delivered through captured details.
        checkpoint: Original call-budget check, consulted within bounded work batches.

    Returns:
        Ordered display-comparison nodes. Original projections, atoms, native
        facts and the positions available through details remain unchanged.
    """
    if checkpoint is not None:
        checkpoint()
    work = 0

    def checked(items):
        nonlocal work
        for item in items:
            work += 1
            if checkpoint is not None and work % 128 == 0:
                checkpoint()
            yield item

    result: list[PreparedNode] = []
    native = {node["node"]: node for node in checked(snapshot.facts["nodes"])}
    layouts = {}
    for root in checked(roots):
        if root.interface is not None:
            signatures = interface_layout_signatures(root.interface, checkpoint=checkpoint)
            layouts.update(checked(signatures.items()))
    field_layouts = {}
    group_layouts = {}
    readable_layouts = {}
    outside_window_fields: set[str] = set()
    selections = (
        selection_summary_plan(snapshot.facts, snapshot.refs, roots=snapshot.roots)
        if defer_inactive_selections
        else None
    )
    detail_selections: set[str] = set()

    for root in checked(roots):
        if root.source is not None:
            outside_window_fields.update(root.source.outside_window_fields)
        if root.atoms is not None and selections is not None:
            for claim in checked(root.atoms.field_claims):
                entry = selections.entries.get(claim.ref)
                inactive_selection_claim = (
                    bool(claim.kind == "text-selection-summary")
                    and bool(claim.field == "selectionRange")
                    and bool(claim.target == claim.ref)
                )
                inactive_selection_claim = inactive_selection_claim and entry is not None and bool(entry.inactive)
                if (
                    inactive_selection_claim
                    and isinstance(claim.value, Mapping)
                    and selections.covers(claim.ref, claim.value)
                ):
                    detail_selections.add(claim.ref)
        if root.readable is not None:
            for ref, style in checked(root.readable.nodes.items()):
                readable_layouts[ref] = (
                    (
                        style.note_slots,
                        style.block_slots,
                        style.note_actions,
                        style.toggle_slot,
                        style.glyph,
                        style.notes_only,
                        style.visual_row,
                    ),
                    tuple(
                        (
                            key,
                            heading.ref,
                            (heading.caption.key, heading.caption.value) if heading.caption is not None else None,
                            heading.sources,
                            heading.shared_index,
                            heading.transparent,
                        )
                        for key, heading in checked(root.readable.headings.items())
                        if heading.ref == ref or ref in heading.sources
                    ),
                )
        if root.groups is not None:
            for group in checked(root.groups.groups):
                signature = (
                    group.kind,
                    group.projected_refs,
                    group.captured_records,
                    tuple(
                        (part.ref, part.slot_key, part.start, part.end) for part in checked((*group.head, *group.tail))
                    ),
                    tuple((entry.ref, entry.slot_key) for entry in checked(group.outline)),
                )
                if isinstance(group, BackgroundOverview):
                    signature += (
                        group.window_ref,
                        group.blockers,
                        group.directory_complete,
                        group.important_refs,
                        group.uncertain_refs,
                        group.nested_windows,
                        group.state_facts,
                    )
                group_layouts.update((ref, signature) for ref in checked(group.projected_refs))
        if root.fields is not None:
            for plan in checked(root.fields.fields.values()):
                field_layouts[plan.target_ref] = tuple(
                    (slot.key, slot.label, slot.value, slot.format, slot.placement)
                    for slot in checked(plan.target_slots)
                )
                for member in checked(plan.members):
                    field_layouts[member.ref] = (plan.target_ref, member.mode, member.consumed_slots)
        # Table prefix sharing and packing only affect the full view. The
        # terminal renderer retains complete, unshared atoms for each diff
        # element, including row/column fields. A changed packing candidate must
        # not mark every otherwise unchanged cell as modified.

    def visit(node: Mapping[str, Any], parent: str | None, depth: int) -> None:
        fields = {key: value for key, value in checked(node.items()) if key not in ("children", "native")}
        selection = fields.get("selectionRange")
        if node["ref"] in detail_selections and isinstance(selection, Mapping) and selections is not None:
            if selections.covers(node["ref"], selection):
                fields.pop("selectionRange")
        if node["ref"] in layouts:
            fields["_interfaceLayout"] = layouts[node["ref"]]
        if node["ref"] in field_layouts:
            fields["_interfaceField"] = field_layouts[node["ref"]]
        if node["ref"] in group_layouts:
            fields["_interfaceDisclosure"] = group_layouts[node["ref"]]
        if node["ref"] in readable_layouts:
            fields["_interfaceReadable"] = readable_layouts[node["ref"]]
        if node["ref"] in outside_window_fields:
            fields["_boundsOutsideWindow"] = True
        # _interfaceLayout is produced as a recursively immutable tuple of
        # layout scalars/tuples. Recopying its shared ancestor path for every
        # descendant adds no isolation. Other fields retain normal detachment.
        layout = fields.pop("_interfaceLayout") if node["ref"] in layouts else None
        captured_fields = _copy_collections(fields, immutable=True, checkpoint=checkpoint)
        if layout is not None:
            captured_fields = MappingProxyType({**captured_fields, "_interfaceLayout": layout})
        result.append(PreparedNode(node["ref"], parent, depth, captured_fields))
        for child in checked(node["children"]):
            visit(child, node["ref"], depth + 1)

    try:
        for root in checked(roots):
            if root.projection is None:
                for ref in checked(root.members):
                    facts = {
                        key: value for key, value in checked(display_node_facts(native[snapshot.refs[ref]]).items())
                    }
                    facts["_relations"] = tuple(
                        relation
                        for relation in checked(snapshot.facts["relations"])
                        if relation["source"] == snapshot.refs[ref]
                    )
                    result.append(
                        PreparedNode(ref, None, 0, _copy_collections(facts, immutable=True, checkpoint=checkpoint))
                    )
            else:
                for node in checked(root.projection["roots"]):
                    visit(node, None, 0)
        return tuple(result)
    finally:
        # The self-referential closure also owns the full captured snapshot;
        # stop retaining it as soon as this comparison traversal is complete.
        visit = None


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
