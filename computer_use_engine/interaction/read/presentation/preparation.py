# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable display projections prepared before delivery and text rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from computer_use.interaction.read.presentation.fallback import display_node_facts
from computer_use.interaction.read.presentation.interface.disclosure.groups.model import BackgroundOverview
from computer_use.interaction.read.presentation.interface.signatures import interface_layout_signatures
from computer_use.interaction.read.presentation.ir import ProjectionInput, ProjectionResult, freeze, thaw
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.selection_summary import selection_summary_plan

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
    from computer_use.interaction.read.presentation.interface.collections.model import (
        CollectionDocument,
        CollectionPrefixPlan,
    )
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


def _copy_collections(value: Any, *, immutable: bool) -> Any:
    """Detach nested fact collections while retaining their shared-child identity.

    Iterative traversal supports deep captured trees and rejects collection cycles.
    Container categories are cached per concrete type; atomic values, including
    immutable native records, retain their identity and skip the traversal stack.

    Args:
        value: Captured facts or a nested collection to copy. Mapping keys stay intact.
        immutable: Produce mapping proxies, tuples, and frozen sets when true;
            otherwise produce dictionaries, lists, and sets.

    Returns:
        A detached collection graph, or the original value when it is atomic.

    Raises:
        ValueError: A collection contains itself through its descendants.
    """
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
    pending: list[tuple[Any, bool]] = [(value, False)]
    while pending:
        current, finish = pending.pop()
        identity = id(current)
        if identity in completed:
            continue
        kind = category(current)
        if not finish:
            if identity in active:
                raise ValueError("Captured presentation facts contain a collection cycle")
            active.add(identity)
            pending.append((current, True))
            children = current.values() if kind == mapping else current
            pending.extend((child, False) for child in reversed(tuple(children)) if category(child))
            continue

        def copied(item: Any) -> Any:
            return completed[id(item)] if category(item) else item

        if kind == mapping:
            mapping_copy = {key: copied(item) for key, item in current.items()}
            completed[identity] = MappingProxyType(mapping_copy) if immutable else mapping_copy
        elif kind == setlike:
            copied_items = (copied(item) for item in current)
            completed[identity] = frozenset(copied_items) if immutable else set(copied_items)
        else:
            sequence_copy = [copied(item) for item in current]
            completed[identity] = tuple(sequence_copy) if immutable else sequence_copy
        active.remove(identity)
    return completed[id(value)]


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
    ) -> PreparedRoot:
        return cls(
            root_ref,
            members,
            _copy_collections(projection, immutable=True),
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
        native_nodes = {
            ref: node
            for root in self.roots
            if root.projection is not None
            for ref, node in root.projection["nativeNodes"].items()
        }
        projected_nodes = {}
        pending = [node for root in self.roots if root.projection is not None for node in root.projection["roots"]]
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
            if (projected is not None and unexpanded_menu(projected)) or (
                partial and "childrenStart" in facts and "nextChildOffset" in facts
            ):
                refs.append(node.ref)
        return tuple(sorted(refs))


def prepared_nodes(
    roots: tuple[PreparedRoot, ...], snapshot: CapturedSnapshot, *, defer_inactive_selections: bool = False
) -> tuple[PreparedNode, ...]:
    """Index the fields and containment used to compare the final display.

    Args:
        roots: Frozen display projections, original atoms and source coverage.
        snapshot: Unchanged captured facts and their original reference bindings.
        defer_inactive_selections: Match the default compact delivery policy. A
            complete unfocused collapsed range may be excluded from comparison
            only when its exact atom claim is delivered through captured details.

    Returns:
        Ordered display-comparison nodes. Original projections, atoms, native
        facts and the positions available through details remain unchanged.
    """
    result: list[PreparedNode] = []
    native = {node["node"]: node for node in snapshot.facts["nodes"]}
    layouts = {
        ref: signature
        for root in roots
        if root.interface is not None
        for ref, signature in interface_layout_signatures(root.interface).items()
    }
    field_layouts = {}
    collection_layouts = {}
    group_layouts = {}
    readable_layouts = {}
    selections = (
        selection_summary_plan(thaw(snapshot.facts), snapshot.refs, roots=snapshot.roots)
        if defer_inactive_selections
        else None
    )
    detail_selections: set[str] = set()

    def collection_prefix(prefix: CollectionPrefixPlan | None) -> tuple[object, ...] | None:
        if prefix is None:
            return None
        return (
            prefix.role.label,
            tuple(state.label for state in prefix.states),
            tuple((slot.key, slot.label, slot.value, slot.format) for slot in prefix.slots),
            tuple((member.ref, member.state_indices, member.slot_keys) for member in prefix.members),
        )

    for root in roots:
        if root.atoms is not None and selections is not None:
            for claim in root.atoms.field_claims:
                entry = selections.entries.get(claim.ref)
                if (
                    claim.kind == "text-selection-summary"
                    and claim.field == "selectionRange"
                    and claim.target == claim.ref
                    and entry is not None
                    and entry.inactive
                    and isinstance(claim.value, Mapping)
                    and selections.covers(claim.ref, claim.value)
                ):
                    detail_selections.add(claim.ref)
        if root.readable is not None:
            for ref, style in root.readable.nodes.items():
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
                        for key, heading in root.readable.headings.items()
                        if heading.ref == ref or ref in heading.sources
                    ),
                )
        if root.groups is not None:
            for group in root.groups.groups:
                signature = (
                    group.kind,
                    group.projected_refs,
                    group.captured_records,
                    tuple((part.ref, part.slot_key, part.start, part.end) for part in (*group.head, *group.tail)),
                    tuple((entry.ref, entry.slot_key) for entry in group.outline),
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
                group_layouts.update((ref, signature) for ref in group.projected_refs)
        if root.fields is not None:
            for plan in root.fields.fields.values():
                field_layouts[plan.target_ref] = tuple(
                    (slot.key, slot.label, slot.value, slot.format, slot.placement) for slot in plan.target_slots
                )
                for member in plan.members:
                    field_layouts[member.ref] = (plan.target_ref, member.mode, member.consumed_slots)
        if root.collections is not None:
            for table in root.collections.tables.values():
                for row in table.rows:
                    for ref in (
                        row.ref,
                        *(source for cell in row.cells for source in (cell.ref, *cell.descendant_refs)),
                    ):
                        collection_layouts[ref] = (
                            table.ref,
                            table.layout,
                            row.ref,
                            collection_prefix(table.row_prefix),
                            tuple(collection_prefix(prefix) for prefix in table.column_prefixes),
                            table.column_labels,
                            tuple(
                                (item.ref, item.slot.key, item.slot.value, item.slot.facts)
                                for item in table.detail_fields
                            ),
                        )

    def visit(node: Mapping[str, Any], parent: str | None, depth: int) -> None:
        fields = {key: value for key, value in node.items() if key not in ("children", "native")}
        selection = fields.get("selectionRange")
        if (
            node["ref"] in detail_selections
            and isinstance(selection, Mapping)
            and selections is not None
            and selections.covers(node["ref"], selection)
        ):
            fields.pop("selectionRange")
        if node["ref"] in layouts:
            fields["_interfaceLayout"] = layouts[node["ref"]]
        if node["ref"] in field_layouts:
            fields["_interfaceField"] = field_layouts[node["ref"]]
        if node["ref"] in collection_layouts:
            fields["_interfaceCollection"] = collection_layouts[node["ref"]]
        if node["ref"] in group_layouts:
            fields["_interfaceDisclosure"] = group_layouts[node["ref"]]
        if node["ref"] in readable_layouts:
            fields["_interfaceReadable"] = readable_layouts[node["ref"]]
        result.append(PreparedNode(node["ref"], parent, depth, freeze(fields)))
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
