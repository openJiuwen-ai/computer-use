# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only source facts and a locally editable presentation forest.

An edit changes only presentation. Native routes and source keys cannot be
written through this interface; rendering materializes the existing dictionary
contract once after all rules have completed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Iterator, Literal

    from computer_use.interaction.read.presentation.compression.caption_equivalence import CaptionEquivalence
    from computer_use.interaction.read.presentation.records import NodeView, SemanticNode
    from computer_use.pipeline.core import T
    from computer_use.protocol.records import Record
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NewType

from computer_use.interaction.read.presentation.policy import display_copy
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex
from computer_use.interaction.read.presentation.tree import interaction_view, unique, with_interactions

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


@dataclass(frozen=True)
class SharedDescription:
    """One exact description value declared for explicit sources in one scope.

    Attributes:
        scope: Retained window, sheet, or menu scope publishing the declaration.
        attribute: The exact normalized description field shared by these sources.
        value: Complete source value; the initial policy permits only empty strings.
        facts: Independent source-field identities that retain ownership of the value.
    """

    scope: SourceKey
    attribute: str
    value: str
    facts: tuple[FactKey, ...]


@dataclass(frozen=True)
class SourceRoleDeclaration:
    """Exact role fields delivered for folded sources under their original references.

    Attributes:
        scope: Original root boundary shared by the declared sources.
        attribute: Role field represented by this declaration.
        value: Complete role text shared by the source fields.
        facts: Original source fields whose roles the declaration preserves.
    """

    scope: SourceKey
    attribute: str
    value: str
    facts: tuple[FactKey, ...]


@dataclass(frozen=True)
class SharedDescriptionDestination:
    """Source-field destination in a shared description declaration.

    Attributes:
        fact: Original field retained by the shared declaration.
        scope: Native root boundary containing that field.
        value: Complete description value delivered for this source.
    """

    fact: FactKey
    scope: SourceKey
    value: str


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
        native_nodes: Semantic nodes before display compression, indexed by their
            retained references. Raw native records remain in the captured snapshot.
        display_nodes: Immutable per-node display fields before compression.
        parents: Observed source-parent relationships; display folding does not rewrite them.
        scopes: Source scope/window identity for each node.
        order: Original source traversal order.
        root: Root of the requested semantic view; native bindings remain separate.
        context: Observation scope and rendering options copied for this projection.
        geometry: Read-only comparison bounds; never exported as display fields or input targets.
    """

    native_nodes: Mapping[str, NodeView]
    display_nodes: Mapping[str, NodeView]
    parents: Mapping[str, str]
    scopes: Mapping[str, str]
    order: tuple[str, ...]
    root: SourceKey
    context: Mapping[str, Any]
    geometry: GeometryIndex = field(default_factory=lambda: GeometryIndex({}))

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
        native, nodes, parents, scopes, order = {}, {}, {}, {}, []

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
            order.append(ref)
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
            tuple(order),
            SourceKey(root["ref"]),
            freeze(context),
        )


# The fields whose presentation can be moved or composed. Execution identity,
# role, capabilities, actions and control membership never belong in this set.
DISPLAY_FIELDS = frozenset(
    ("name", "description", "value", "selectionRange", "selectedText", "valueDescription", "rangeDescription")
)
ANNOTATIONS = frozenset(
    (
        "labelTargets",
        "linkedLabel",
        "editorOwnerRef",
        "outlineDepth",
        "layoutGroups",
        "spatialPosition",
        "preserveGroup",
        "visualChainChild",
        "visualLabelRef",
        "_contextRefs",
    )
)


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
        shared_descriptions: Exact field groups declared by this retained scope node.
        shared_description_fields: This source's explicit routes to those declarations.
        source_roles: Role fields retained for sources folded after sharing their descriptions.
    """

    source: SourceKey
    fields: dict[str, PresentedValue]
    children: list[str]
    annotations: Record = field(default_factory=dict)
    text_sources: list[Record] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    shared_descriptions: list[SharedDescription] = field(default_factory=list)
    source_roles: list[SourceRoleDeclaration] = field(default_factory=list)
    shared_description_fields: dict[str, SharedDescriptionDestination] = field(default_factory=dict)


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
        caption_equivalences: Native captions and ordered passive components folded
            without changing either source field's provenance.
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
    caption_equivalences: list[CaptionEquivalence] = field(default_factory=list)

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
        if key == "sharedDescriptions" and node.shared_descriptions:
            return tuple(node.shared_descriptions)
        if key == "sourceRoles" and node.source_roles:
            return tuple(node.source_roles)
        if key == "sharedDescriptionFields" and node.shared_description_fields:
            return MappingProxyType(node.shared_description_fields)
        if key in node.fields:
            return node.fields[key].value
        if key in node.annotations:
            return freeze(node.annotations[key])
        return source.display_nodes[self._source][key]

    def __iter__(self) -> Iterator[str]:
        source = self._ir.source.display_nodes[self._source]
        node = self._ir.nodes[self._source]
        shared = (
            (("sharedDescriptions",) if node.shared_descriptions else ())
            + (("sharedDescriptionFields",) if node.shared_description_fields else ())
            + (("sourceRoles",) if node.source_roles else ())
        )
        return iter(
            dict.fromkeys(
                (*source, *node.fields, *node.annotations, *shared, "children", "native", "sourceRefs", "textSources")
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
    def geometry(self) -> GeometryIndex:
        """Return immutable comparison bounds for local presentation decisions."""
        return self._ir.source.geometry

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

    def field_value(self, ref: str, field: str) -> PresentedValue | None:
        """Return the current displayed value and its exact source-field provenance."""
        return self._ir.nodes[ref].fields.get(field)

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

    def share_description(self, group: SharedDescription) -> None:
        """Publish exact source fields once at their retained native scope.

        The caller supplies completeness evidence. This editor verifies ownership,
        value identity, and the stable scope before recording any display destination.
        """
        if (
            group.attribute not in ("valueDescription", "rangeDescription")
            or group.value != ""
            or type(group.value) is not str
            or len(group.facts) < 2
            or group.scope not in self._ir.nodes
            or self._ir.source.native_nodes[group.scope]["role"] not in ("window", "sheet", "menuBar")
            or len(set(group.facts)) != len(group.facts)
        ):
            raise ValueError("Invalid shared description declaration")
        for fact in group.facts:
            node = self._ir.source.native_nodes.get(fact.source, {})
            if (
                fact.attribute != group.attribute
                or fact.attribute not in node
                or type(node[fact.attribute]) is not str
                or node[fact.attribute] != group.value
                or self._ir.source.scopes.get(fact.source) != group.scope
                or fact.attribute in self._ir.nodes[fact.source].shared_description_fields
            ):
                raise ValueError("Shared description has no unique matching scoped source")
        self._ir.nodes[group.scope].shared_descriptions.append(group)
        for fact in group.facts:
            self._ir.nodes[fact.source].shared_description_fields[fact.attribute] = SharedDescriptionDestination(
                fact, group.scope, group.value
            )
            self.event(sourceRef=fact.source, kind="shared-description", targetRef=group.scope, field=fact.attribute)

    def declare_source_roles(self, group: SourceRoleDeclaration) -> None:
        """Retain exact role fields after their source nodes are folded from the tree."""
        if (
            group.attribute not in ("role", "roleDescription")
            or type(group.value) is not str
            or group.scope not in self._ir.nodes
            or not group.facts
            or len(set(group.facts)) != len(group.facts)
        ):
            raise ValueError("Invalid source role declaration")
        for fact in group.facts:
            source = self._ir.source.native_nodes.get(fact.source, {})
            if (
                fact.attribute != group.attribute
                or fact.attribute not in source
                or type(source[fact.attribute]) is not str
                or source[fact.attribute] != group.value
                or self._ir.source.scopes.get(fact.source) != group.scope
                or fact.source not in self._ir.elisions
                or not self._ir.nodes[fact.source].shared_description_fields
            ):
                raise ValueError("Source role has no matching folded scoped field")
        self._ir.nodes[group.scope].source_roles.append(group)

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

    def adopt(
        self,
        target: NodeView,
        parts: Sequence[Record],
        name: str,
        separator: str = ", ",
        *,
        preserve_occurrences: bool = False,
    ) -> None:
        """Compose source-backed text parts into a display field.

        Args:
            target: Display node receiving the composed field.
            parts: Caption parts with source reference, attribute, and unchanged text.
            name: Editable display field receiving the composed value.
            separator: Text inserted between distinct caption parts.
            preserve_occurrences: Keep ordered text occurrences separate, including
                equal strings from different sources in a composed visual text line.
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
        if preserve_occurrences:
            values = tuple(
                ValuePart(part["text"], (FactKey(SourceKey(part["sourceRef"]), part["attribute"]),)) for part in parts
            )
        else:
            grouped = {}
            for part in node.text_sources:
                if part["field"] == name:
                    grouped.setdefault(part["text"], []).append(
                        FactKey(SourceKey(part["sourceRef"]), part["attribute"])
                    )
            values = tuple(ValuePart(text, tuple(facts)) for text, facts in grouped.items())
        presented = PresentedValue(values, separator)
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
        if (kind == "fold" and len(destination_refs) != 1) or (kind == "empty" and destination_refs):
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

    def fold_caption_components(self, proof: CaptionEquivalence) -> None:
        """Fold exact passive components while retaining the owner's native caption source.

        Component fields remain unclaimed by the owner. Native field and relationship
        accounting therefore delivers their original references and values in captured
        details, alongside their unchanged action catalogues.

        Args:
            proof: Validated ordered composition of complete passive source fields.
        """
        view = ProjectionView(self._ir)
        owner = view.nodes[proof.owner]
        current = view.field_value(proof.owner, proof.display_field)
        if (
            current is None
            or len(current.parts) != 1
            or current.parts[0].facts != (proof.anchor,)
            or proof.anchor.source != proof.owner
            or proof.anchor.attribute not in ("name", "description")
            or view.scopes[proof.owner] != proof.scope
            or view.native_nodes[proof.owner].get(proof.anchor.attribute) != proof.composition.value
            or current.value != proof.composition.value
            or len(proof.composition.parts) < 2
        ):
            raise ValueError("Caption composition has no matching native owner field")
        refs = []
        for part in proof.composition.parts:
            if len(part.facts) != 1:
                raise ValueError("Caption component needs one exact source field")
            fact = part.facts[0]
            if (
                fact.attribute != "value"
                or fact.source == proof.owner
                or fact.source in refs
                or view.scopes.get(fact.source) != proof.scope
                or view.native_nodes.get(fact.source, {}).get(fact.attribute) != part.value
                or fact.source not in [child["ref"] for child in owner["children"]]
            ):
                raise ValueError("Caption component is not an independent direct display source")
            refs.append(fact.source)
        # The complete original paths were checked by the rule; display order must
        # still agree after all earlier compression and spatial organization.
        if [child["ref"] for child in owner["children"] if child["ref"] in refs] != refs:
            raise ValueError("Caption component display order changed")
        for ref in refs:
            self.fold(view.nodes[ref], owner, "caption-component")
        self.children(owner, [child for child in owner["children"] if child["ref"] not in refs])
        self._ir.caption_equivalences.append(proof)
        self.event(
            sourceRef=proof.owner,
            kind="native-caption-components",
            scope=proof.scope,
            field=proof.display_field,
            anchor=dict(sourceRef=proof.anchor.source, attribute=proof.anchor.attribute, value=current.value),
            separator=proof.composition.separator,
            components=[
                dict(
                    sourceRef=part.facts[0].source,
                    attribute=part.facts[0].attribute,
                    value=part.value,
                    detailField="control_value.value",
                )
                for part in proof.composition.parts
            ],
        )

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
    visual_labels = set()
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
        groups = node.get("layoutGroups", ())
        if groups:
            members = [ref for group in groups for ref in group["members"]]
            if (
                members != [child["ref"] for child in node["children"]]
                or len(set(members)) != len(members)
                or any(group["kind"] != "row" or len(group["members"]) < 2 for group in groups)
                or any(view.scopes[ref] != view.scopes[node["ref"]] for ref in members)
            ):
                raise ValueError("Layout groups must partition the original scoped element references")
        if any(ref not in view.native_nodes for ref in node.get("_contextRefs", ())):
            raise ValueError("Layout context has an unknown source reference")
        label_ref = node.get("visualLabelRef")
        if label_ref is not None:
            if (
                not isinstance(label_ref, str)
                or label_ref not in view.native_nodes
                or label_ref == ref
                or view.scopes[label_ref] != view.scopes[ref]
            ):
                raise ValueError("Visual labels require a distinct source reference in the same scope")
            visual_labels.add(label_ref)
        child_ref = node.get("visualChainChild")
        if child_ref is not None:
            if (
                not isinstance(child_ref, str)
                or len(node["children"]) != 1
                or child_ref != node["children"][0]["ref"]
                or view.scopes[child_ref] != view.scopes[ref]
            ):
                raise ValueError("Visual chains require one direct child in the same source scope")
        pending.extend(node["children"])
    if not visual_labels.issubset(seen):
        raise ValueError("Visual label references must retain their independent display nodes")
    shared_fields: dict[FactKey, SharedDescriptionDestination] = {}
    for owner in ir.nodes.values():
        for group in owner.shared_descriptions:
            if group.scope != owner.source or group.scope not in seen:
                raise ValueError("Shared description scope must remain visible")
            for fact in group.facts:
                source = view.native_nodes.get(fact.source, {})
                expected = SharedDescriptionDestination(fact, group.scope, group.value)
                if (
                    fact in shared_fields
                    or fact.attribute != group.attribute
                    or view.scopes.get(fact.source) != group.scope
                    or fact.attribute not in source
                    or type(source[fact.attribute]) is not str
                    or source[fact.attribute] != group.value
                    or ir.nodes[fact.source].shared_description_fields.get(fact.attribute) != expected
                ):
                    raise ValueError("Shared description source or destination mismatch")
                shared_fields[fact] = expected
    if any(
        shared_fields.get(destination.fact) != destination
        for node in ir.nodes.values()
        for destination in node.shared_description_fields.values()
    ):
        raise ValueError("Shared description field lacks a public scope declaration")
    role_facts = set()
    for owner in ir.nodes.values():
        for group in owner.source_roles:
            if group.scope != owner.source or group.scope not in seen:
                raise ValueError("Source role declaration scope must remain visible")
            for fact in group.facts:
                source = view.native_nodes.get(fact.source, {})
                if (
                    fact in role_facts
                    or fact.source in seen
                    or fact.source not in ir.elisions
                    or fact.attribute != group.attribute
                    or view.scopes.get(fact.source) != group.scope
                    or fact.attribute not in source
                    or type(source[fact.attribute]) is not str
                    or source[fact.attribute] != group.value
                ):
                    raise ValueError("Source role declaration must retain an exact folded field")
                role_facts.add(fact)
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
    for proof in ir.caption_equivalences:
        value = view.field_value(proof.owner, proof.display_field)
        if (
            proof.owner not in seen
            or view.scopes[proof.owner] != proof.scope
            or value is None
            or value.value != proof.composition.value
            or len(value.parts) != 1
            or value.parts[0].facts != (proof.anchor,)
            or any(
                len(part.facts) != 1
                or part.facts[0].source in seen
                or ir.folded_into.get(part.facts[0].source) != proof.owner
                or view.native_nodes[part.facts[0].source].get(part.facts[0].attribute) != part.value
                for part in proof.composition.parts
            )
        ):
            raise ValueError("Native caption composition lost its source or display destination")


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
    provenance = {(ref, name): value for ref, node in ir.nodes.items() for name, value in node.fields.items()}
    for node in ir.nodes.values():
        for group in node.source_roles:
            for fact in group.facts:
                provenance[fact.source, fact.attribute] = PresentedValue((ValuePart(group.value, (fact,)),))
    return ProjectionResult(result, ir.source, provenance)


if TYPE_CHECKING:
    from typing import Sequence
