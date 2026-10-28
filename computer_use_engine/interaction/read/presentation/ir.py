"""Read-only source facts and a locally editable presentation forest.

An edit changes only presentation. Native routes and source keys cannot be
written through this interface; rendering materializes the existing dictionary
contract once after all rules have completed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, NewType

from computer_use.interaction.read.presentation.policy import display_copy
from computer_use.interaction.read.presentation.tree import interaction_view, unique, with_interactions

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Iterator, Literal

    from computer_use.interaction.read.presentation.records import NodeView, SemanticNode
    from computer_use.pipeline.core import T
    from computer_use.protocol.records import Record


SourceKey = NewType("SourceKey", str)


@dataclass(frozen=True)
class FactKey:
    """Exact source-field identity used by the presentation ledger.

    Attributes:
        source: Identity of the source node contributing this fact.
        attribute: Native/display field name recorded by the provenance ledger.
    """

    source: SourceKey
    attribute: str


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return set(value)
    return value


@dataclass(frozen=True)
class ValuePart:
    """An unchanged source value and the facts that justify displaying it.

    Attributes:
        value: Native or projected field value; provider-specific values remain heterogeneous.
        facts: Source node/field keys that justify this displayed value.
    """

    value: object
    facts: tuple[FactKey, ...]


@dataclass(frozen=True)
class PresentedValue:
    """One original value or a documented composition of source values.

    Attributes:
        parts: Source observations collected in the plan's order.
        separator: Join string for composed parts; None preserves the single original value.
    """

    parts: tuple[ValuePart, ...]
    separator: str | None = None

    @property
    def value(self) -> Any:
        if self.separator is None:
            return self.parts[0].value
        return self.separator.join(part.value for part in self.parts)


@dataclass(frozen=True)
class Elision:
    """Recorded display omission with explicit destinations and supporting facts.

    Attributes:
        kind: Declared local branch or representation kind.
        destinations: Display source identities that represent the omitted node.
        rule: Compression rule that recorded the omission.
        evidence: Source facts used to justify the omission.
    """

    kind: str
    destinations: tuple[SourceKey, ...]
    rule: str
    evidence: tuple[FactKey, ...] = ()


@dataclass(frozen=True)
class ProjectionInput:
    """Immutable source facts and source hierarchy for one display projection.

    Attributes:
        native_nodes: Original native-node facts indexed by their exact native tokens.
        display_nodes: Immutable per-node display fields before compression.
        parents: Observed source-parent relationships; display folding does not rewrite them.
        scopes: Source scope/window identity for each node.
        order: Original source traversal order.
        root: Root of the requested semantic view; native bindings remain separate.
        context: Observation scope and rendering options copied for this projection.
    """

    native_nodes: Mapping[str, NodeView]
    display_nodes: Mapping[str, NodeView]
    parents: Mapping[str, str]
    scopes: Mapping[str, str]
    order: tuple[str, ...]
    root: SourceKey
    context: Mapping[str, Any]

    @classmethod
    def build(cls, root: NodeView, context: Mapping[str, Any] | None = None) -> ProjectionInput:
        """Freeze source facts and index the original scope/parent relationships.

        Args:
            root: Root of the requested semantic view; native bindings remain separate.
            context: Existing observation and rendering context, copied for this projection.

        Returns:
            The immutable source graph retaining the existing source identities.
        """
        context = {} if context is None else dict(context)
        native, nodes, parents, scopes, traversal_order = {}, {}, {}, {}, []

        def source(node: NodeView) -> None:
            native[node["ref"]] = node
            for child in node["children"]:
                source(child)

        source(freeze(root))

        def display(node: NodeView, scope: str) -> None:
            """Index display fields, source parents, and the nearest window or menu scope.

            Args:
                node: Source/projected node in this exact view; its native identity is
                    unchanged.
                scope: Local compression traversal context; independent regions retain their
                    boundaries.
            """
            ref = node["ref"]
            if node["role"] in ("window", "sheet", "menuBar"):
                scope = ref
            projected = display_copy(node) if context.get("nativeFacts") else node
            nodes[ref] = freeze({key: value for key, value in projected.items() if key != "children"})
            scopes[ref] = scope
            traversal_order.append(ref)
            for child in node["children"]:
                parents[child["ref"]] = ref
                display(child, scope)

        display(
            interaction_view(
                with_interactions(root, context.get("inheritedBlocks")), bool(context.get("includeBackground"))
            ),
            root["ref"],
        )
        return cls(
            MappingProxyType(native),
            MappingProxyType(nodes),
            MappingProxyType(parents),
            MappingProxyType(scopes),
            tuple(traversal_order),
            SourceKey(root["ref"]),
            freeze(context),
        )


# The fields whose presentation can be moved or composed. Execution identity,
# role, capabilities, actions and control membership never belong in this set.
DISPLAY_FIELDS = frozenset(("name", "description", "value", "selectionRange", "selectedText"))
ANNOTATIONS = frozenset(("labelTargets", "linkedLabel", "editorOwnerRef", "outlineDepth"))


@dataclass
class _Node:
    """Editable display fields and child order for one immutable source identity.

    Attributes:
        source: Immutable identity of the node represented by this display entry.
        fields: Locally editable display values with their source-fact provenance.
        children: Ordered source identities of this display node's children.
        annotations: Derived label, editor-owner, and outline relationships.
        text_sources: Exact native field provenance for composed caption parts.
        source_refs: Source references represented by this local presentation node.
    """

    source: SourceKey
    fields: dict[str, PresentedValue]
    children: list[str]
    annotations: Record = field(default_factory=dict)
    text_sources: list[Record] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)


@dataclass
class PresentationIR:
    """Locally editable display structure with a complete source-provenance ledger.

    Attributes:
        source: Immutable source facts and source hierarchy for one display projection.
        roots: Ordered roots in this observed or projected view.
        nodes: Mutable display nodes indexed by their original source identities.
        elisions: Recorded omissions and their source-preserving destinations.
        changes: Local presentation edits recorded for audit/coverage.
        folded_into: Final display destinations indexed by their folded source references.
        targets: Source-to-destination routes established while finalizing the projection.
        stages: Per-rule call counts, change counts, and affected source references.
        resolved_elisions: Final visible destinations for each omitted source identity.
        active_stage: Current local rule diagnostic record, restored after the rule finishes.
        active_rule: Name of the local presentation rule currently recording edits.
        views: Read-only node views cached only for this presentation IR.
    """

    source: ProjectionInput
    roots: list[str]
    nodes: dict[str, _Node]
    elisions: dict[str, Elision] = field(default_factory=dict)
    changes: list[Record] = field(default_factory=list)
    folded_into: dict[str, str] = field(default_factory=dict)
    targets: dict[str, Record] = field(default_factory=dict)
    stages: dict[str, Record] = field(default_factory=dict)
    resolved_elisions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    active_stage: Record | None = None
    active_rule: str = ""
    views: dict[str, ViewNode] = field(default_factory=dict)

    @classmethod
    def build(cls, source: ProjectionInput) -> PresentationIR:
        """Create local display fields while retaining the immutable source graph.

        Args:
            source: Immutable source facts and source hierarchy for one display projection.

        Returns:
            The local presentation IR retaining the existing source identities.
        """
        children = {ref: [] for ref in source.display_nodes}
        for child, parent in source.parents.items():
            children[parent].append(SourceKey(child))
        nodes = {}
        for ref, native in source.display_nodes.items():
            fields = {
                name: PresentedValue((ValuePart(native[name], (FactKey(SourceKey(ref), name),)),))
                for name in DISPLAY_FIELDS
                if name in native
            }
            parts = [
                dict(sourceRef=ref, attribute=name, field=name, text=native[name])
                for name in ("name", "description")
                if native.get(name)
            ]
            nodes[ref] = _Node(SourceKey(ref), fields, children[ref], text_sources=parts, source_refs=[ref])
        return cls(source, [source.root], nodes)


class ViewNode(Mapping):
    """A live read-only view. Its nested values are recursively immutable."""

    def __init__(self, ir: PresentationIR, source: SourceKey) -> None:
        self._ir, self._source = ir, source

    def __getitem__(self, key: str) -> Any:
        """Getitem.

        Args:
            key: Stable scope/source key used by the existing owner.
        """
        node, source = self._ir.nodes[self._source], self._ir.source
        if key == "ref":
            return self._source
        if key == "children":
            return tuple(view_node(self._ir, ref) for ref in node.children)
        if key == "native":
            return source.native_nodes[self._source]
        if key == "sourceRefs":
            return tuple(node.source_refs)
        if key == "textSources":
            return freeze(node.text_sources)
        if key in node.fields:
            return node.fields[key].value
        if key in node.annotations:
            return freeze(node.annotations[key])
        return source.display_nodes[self._source][key]

    def __iter__(self) -> Iterator[str]:
        source = self._ir.source.display_nodes[self._source]
        return iter(
            dict.fromkeys(
                (
                    *source,
                    *self._ir.nodes[self._source].fields,
                    *self._ir.nodes[self._source].annotations,
                    "children",
                    "native",
                    "sourceRefs",
                    "textSources",
                )
            )
        )

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ViewNode):
            return self._ir is other._ir and self._source == other._source
        return super().__eq__(other)


def view_node(ir: PresentationIR, ref: str) -> ViewNode:
    if ref not in ir.views:
        ir.views[ref] = ViewNode(ir, ref)
    return ir.views[ref]


class ProjectionView:
    """Expose immutable node views and provenance to compression rules."""

    def __init__(self, ir: PresentationIR) -> None:
        self._ir = ir
        self._nodes = MappingProxyType({ref: view_node(ir, ref) for ref in ir.nodes})

    @property
    def context(self) -> Mapping[str, Any]:
        return self._ir.source.context

    @property
    def native_nodes(self) -> Mapping[str, NodeView]:
        return self._ir.source.native_nodes

    @property
    def nodes(self) -> Mapping[str, ViewNode]:
        return self._nodes

    @property
    def parents(self) -> Mapping[str, str]:
        return self._ir.source.parents

    @property
    def scopes(self) -> Mapping[str, str]:
        return self._ir.source.scopes

    @property
    def order(self) -> tuple[str, ...]:
        return self._ir.source.order

    @property
    def targets(self) -> Mapping[str, Any]:
        return freeze(self._ir.targets)

    @property
    def roots(self) -> tuple[ViewNode, ...]:
        return tuple(view_node(self._ir, ref) for ref in self._ir.roots)

    @property
    def view(self) -> ViewNode:
        return view_node(self._ir, self._ir.source.root)

    @property
    def folded_into(self) -> Mapping[str, str]:
        return MappingProxyType(self._ir.folded_into)

    @property
    def changes(self) -> tuple[Record, ...]:
        return freeze(self._ir.changes)

    @property
    def expanded(self) -> frozenset[str]:
        return frozenset(
            m["ref"]
            for m in self.native_nodes.get(self.context.get("subtreeRef"), {}).get("control", {}).get("members", ())
        )

    def keep(self, node: NodeView) -> bool:
        return node["ref"] == self.context.get("subtreeRef") or node["ref"] in self.expanded

    @staticmethod
    def member_of(child: NodeView, owner: NodeView) -> bool:
        return any(m["ref"] == child["ref"] for m in owner.get("control", {}).get("members", ()))

    def ancestor_of(self, ancestor: str, child: str) -> bool:
        """Test whether an identity occurs in the child's original source-parent chain."""
        parent = self.parents.get(child)
        while parent is not None:
            if parent == ancestor:
                return True
            parent = self.parents.get(parent)
        return False


class ProjectionEditor:
    """Update local display fields, child order, and the source-provenance ledger."""

    def __init__(self, ir: PresentationIR) -> None:
        self._ir = ir

    def _ref(self, node: NodeView | str) -> str:
        ref = node["ref"] if isinstance(node, Mapping) else node
        if ref not in self._ir.nodes:
            raise ValueError("Unknown presentation source")
        return ref

    def children(self, node: NodeView, children: Iterable[NodeView]) -> None:
        self._ir.nodes[self._ref(node)].children = [self._ref(child) for child in children]

    def roots(self, nodes: Iterable[NodeView]) -> None:
        self._ir.roots = [self._ref(node) for node in nodes]

    def annotate(self, node: NodeView, key: str, value: Any) -> None:
        if key not in ANNOTATIONS:
            raise ValueError("Not a presentation annotation")
        self._ir.nodes[self._ref(node)].annotations[key] = freeze(value)

    def targets(self, targets: Mapping[str, Mapping[str, Any]]) -> None:
        self._ir.targets = {ref: dict(values) for ref, values in targets.items()}

    def set_field(
        self,
        node: NodeView,
        name: str,
        value: Any,
        *,
        source: NodeView | str | None = None,
        attribute: str | None = None,
    ) -> None:
        """Replace one display field with an unchanged value from a source field.

        Args:
            node: Display node receiving the field.
            name: Editable display field to replace.
            value: Value that must match the identified immutable source field.
            source: Source node supplying the value; defaults to the receiving node.
            attribute: Source field name; defaults to the destination field name.
        """
        if name not in DISPLAY_FIELDS:
            raise ValueError("Not a presentation field")
        ref = self._ref(node)
        origin = self._ref(source) if source is not None else ref
        attribute = attribute or name
        original = self._ir.source.native_nodes[origin]
        if original.get(attribute) != value or attribute not in original:
            raise ValueError("Presentation value has no matching source field")
        self._ir.nodes[ref].fields[name] = PresentedValue(
            (ValuePart(freeze(value), (FactKey(SourceKey(origin), attribute),)),)
        )

    def suppress(self, node: NodeView, name: str) -> None:
        if name not in DISPLAY_FIELDS:
            raise ValueError("Not a presentation field")
        self._ir.nodes[self._ref(node)].fields[name] = PresentedValue((), "")

    def text_source(self, node: NodeView, part: Record) -> None:
        source = self._ir.source.native_nodes.get(part["sourceRef"], {})
        if part["attribute"] not in source or source[part["attribute"]] != part["text"]:
            raise ValueError("Projection caption has no matching source field")
        self._ir.nodes[self._ref(node)].text_sources.append(dict(part))

    def adopt(self, target: NodeView, parts: Sequence[Record], name: str, separator: str = ", ") -> None:
        """Compose source-backed text parts into a display field.

        Args:
            target: Display node receiving the composed field.
            parts: Caption parts with source reference, attribute, and unchanged text.
            name: Editable display field receiving the composed value.
            separator: Text inserted between distinct caption parts.
        """
        if name not in DISPLAY_FIELDS:
            raise ValueError("Not a presentation field")
        ref, node = self._ref(target), self._ir.nodes[self._ref(target)]
        for part in parts:
            if not any(
                existing["sourceRef"] == part["sourceRef"]
                and existing["attribute"] == part["attribute"]
                and existing["field"] == name
                and existing["text"] == part["text"]
                for existing in node.text_sources
            ):
                self.text_source(target, {**part, "field": name})
        node.source_refs = unique(node.source_refs + [part["sourceRef"] for part in parts])
        grouped = {}
        for part in node.text_sources:
            if part["field"] == name:
                grouped.setdefault(part["text"], []).append(FactKey(SourceKey(part["sourceRef"]), part["attribute"]))
        presented = PresentedValue(tuple(ValuePart(text, tuple(facts)) for text, facts in grouped.items()), separator)
        if target.get(name) != presented.value and self._ir.active_stage is not None:
            self._ir.active_stage["affectedRefs"].add(ref)
        node.fields[name] = presented

    def event(self, **event: Any) -> None:
        self._ir.changes.append(event)

    def elide(
        self,
        source: NodeView,
        kind: Literal["fold", "splice", "empty"],
        destinations: Iterable[NodeView] = (),
        *,
        event_kind: str = "layout",
        target: NodeView | None = None,
    ) -> None:
        """Record an omitted display node and its source-representation routes.

        Args:
            source: Display node being omitted from the visible hierarchy.
            kind: Omission form: one-destination fold, structural splice, or empty node.
            destinations: Nodes that represent the omitted node's facts.
            event_kind: Category recorded in the projection change ledger.
            target: Optional target reference attached to the change event.
        """
        if kind not in ("fold", "splice", "empty"):
            raise ValueError("Unknown presentation elision kind")
        ref = self._ref(source)
        destination_refs = tuple(SourceKey(self._ref(item)) for item in destinations)
        invalid_destination_refs = (
            True if (kind == "fold" and len(destination_refs) != 1) or (kind == "empty" and destination_refs) else False
        )
        if invalid_destination_refs:
            raise ValueError("Invalid presentation elision destinations")
        self._ir.elisions[ref] = Elision(
            kind, destination_refs, self._ir.active_rule, (FactKey(SourceKey(ref), "children"),)
        )
        event = dict(sourceRef=ref, kind=event_kind)
        if target is not None:
            event["targetRef"] = self._ref(target)
        self.event(**event)

    def fold(self, source: NodeView, target: NodeView, kind: str) -> None:
        """Associate an omitted source node with one retained display destination.

        Args:
            source: Node represented by the retained destination.
            target: Retained node that accumulates the source-reference provenance.
            kind: Change category, such as a folded label or layout wrapper.
        """
        ref, owner = self._ref(source), self._ref(target)
        self._ir.folded_into[ref] = owner
        target_node = self._ir.nodes[owner]
        target_node.source_refs = unique(target_node.source_refs + [ref] + list(source["sourceRefs"]))
        self.elide(source, "fold", (target,), event_kind=kind, target=target)
        if kind == "label" and source.get("actions"):
            self.event(sourceRef=ref, kind="actions", actions=[a["name"] for a in source["actions"]])

    def finalize_folds(self) -> None:
        for ref, owner in self._ir.folded_into.items():
            target, seen = owner, {ref}
            while target in self._ir.folded_into:
                if target in seen:
                    raise ValueError("Projection fold contains a cycle")
                seen.add(target)
                target = self._ir.folded_into[target]
            self._ir.folded_into[ref] = target
        resolved, active = {}, set()

        def destinations(ref: str) -> tuple[str, ...]:
            """Resolve an omitted source through its recorded display destinations.

            Args:
                ref: Display reference from the observation, not an operating-system identifier.
            """
            if ref in resolved:
                return resolved[ref]
            if ref in active:
                raise ValueError("Projection elision contains a cycle")
            if ref not in self._ir.elisions:
                return (ref,)
            active.add(ref)
            result = tuple(
                dict.fromkeys(target for child in self._ir.elisions[ref].destinations for target in destinations(child))
            )
            active.remove(ref)
            resolved[ref] = result
            return result

        for ref in self._ir.elisions:
            destinations(ref)
        self._ir.resolved_elisions = resolved

    def run(self, step: Callable[..., T], view: ProjectionView, *args: object) -> T:
        """Apply one compression rule within its provenance and diagnostic scope.

        Args:
            step: Compression rule called with the view, this editor, and rule arguments.
            view: Read-only view of the current local projection.
            *args: Node, traversal scope, or other arguments required by the rule.

        Returns:
            The rule's result, such as replacement nodes for the traversal.
        """
        previous, previous_rule = self._ir.active_stage, self._ir.active_rule
        self._ir.active_rule = step.__name__
        record, start = None, len(self._ir.changes)
        if view.context.get("recordStages"):
            record = self._ir.stages.setdefault(step.__name__, dict(calls=0, changes=0, affectedRefs=set()))
            self._ir.active_stage = record
            record["calls"] += 1
        try:
            return step(view, self, *args)
        finally:
            if record is not None:
                events = self._ir.changes[start:]
                record["changes"] += len(events)
                record["affectedRefs"].update(event["sourceRef"] for event in events)
            self._ir.active_stage, self._ir.active_rule = previous, previous_rule


def validate_projection(ir: PresentationIR) -> None:
    """Check that each source fact and operation reference has a valid display route.

    Args:
        ir: Locally owned presentation IR, with native source facts kept immutable.
    """
    view, seen, pending = ProjectionView(ir), set(), list(ProjectionView(ir).roots)
    while pending:
        node = pending.pop()
        ref = node["ref"]
        if ref not in ir.nodes or ref in seen:
            raise ValueError("Projection contains an unknown or repeated element reference")
        seen.add(ref)
        if any(source not in view.native_nodes for source in node["sourceRefs"]):
            raise ValueError("Projection contains an unknown source reference")
        for part in node["textSources"]:
            if (
                part["attribute"] not in view.native_nodes.get(part["sourceRef"], {})
                or view.native_nodes[part["sourceRef"]][part["attribute"]] != part["text"]
            ):
                raise ValueError("Projection caption has no matching source field")
        pending.extend(node["children"])
    omitted = set(ir.elisions)
    for ref, elision in ir.elisions.items():
        if ref not in ir.nodes or any(destination not in ir.nodes for destination in elision.destinations):
            raise ValueError("Projection elision has an unknown source or destination")
    if "app" in view.context and view.view["role"] == "application" and not view.context.get("subtreeRef"):
        omitted.add(view.view["ref"])
    if set(ir.nodes) - seen - omitted:
        raise ValueError("Projection removed an element without recording its source")
    for ref, owner in ir.folded_into.items():
        if ref not in view.native_nodes or owner not in view.native_nodes:
            raise ValueError("Projection fold has no native source or target")


class ProjectionResult(dict):
    """Rendered projection dictionary with immutable source and field provenance.

    The source graph and provenance ledger are retained as Python attributes alongside
    the dictionary fields consumed by rendering.
    """

    def __init__(
        self,
        values: Record,
        source: ProjectionInput,
        provenance: Mapping[tuple[str, str], PresentedValue],
    ) -> None:
        super().__init__(values)
        self.source = source
        self.provenance = MappingProxyType(provenance)

    def __deepcopy__(self, memo: dict[int, Any]) -> ProjectionResult:
        from copy import deepcopy

        return ProjectionResult(deepcopy(dict(self), memo), self.source, self.provenance)


def export_projection(ir: PresentationIR) -> ProjectionResult:
    """Export visible nodes together with their source-provenance routes.

    Args:
        ir: Locally owned presentation IR, with native source facts kept immutable.
    """
    validate_projection(ir)
    view = ProjectionView(ir)
    native_nodes = {}

    def index_native(node: SemanticNode) -> None:
        native_nodes[node["ref"]] = node
        for child in node["children"]:
            index_native(child)

    index_native(thaw(view.native_nodes[ir.source.root]))

    def materialize(node: ViewNode) -> SemanticNode:
        result = {key: thaw(value) for key, value in node.items() if key not in ("children", "native")}
        result.update(native=native_nodes[node["ref"]], children=[materialize(child) for child in node["children"]])
        return result

    result = dict(
        roots=[materialize(node) for node in view.roots],
        foldedInto=dict(ir.folded_into),
        nativeNodes=native_nodes,
        changes=thaw(ir.changes),
    )
    if view.context.get("recordStages"):
        result["stages"] = [
            dict(name=name, **{**record, "affectedRefs": sorted(record["affectedRefs"])})
            for name, record in ir.stages.items()
        ]
    return ProjectionResult(
        result, ir.source, {(ref, name): value for ref, node in ir.nodes.items() for name, value in node.fields.items()}
    )


if TYPE_CHECKING:
    from typing import Sequence
