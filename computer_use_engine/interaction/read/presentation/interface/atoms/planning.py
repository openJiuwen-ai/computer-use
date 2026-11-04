# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare ordered source-bound fields without generating or parsing display text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.actions.control_raises import capture_control_raises
from computer_use.interaction.read.presentation.interface.atoms.indices import index_has_collection_context
from computer_use.interaction.read.presentation.actions.menu_mechanisms import capture_menu_mechanisms
from computer_use.interaction.read.presentation.compact import (
    display_actions,
    display_selection,
    embedded_content,
    unexpanded_menu,
)
from computer_use.interaction.read.presentation.compression.descriptions import shared_description
from computer_use.interaction.read.presentation.compression.policy import secondary_actions
from computer_use.interaction.read.presentation.ir import FactKey, SourceKey, freeze
from computer_use.interaction.read.presentation.rendering.native_descriptions import (
    capture_common_role_aliases,
    capture_native_description_fields,
)
from computer_use.interaction.read.presentation.rendering.code_descriptions import capture_code_descriptions
from computer_use.interaction.read.presentation.rendering.selected_text_details import (
    capture_empty_selected_text_fields,
)
from computer_use.interaction.read.presentation.tree import value_label
from computer_use.interaction.read.presentation.selection_summary import SelectionSummaryPlan, selection_summary_plan

from .model import (
    AtomAction,
    AtomCoverage,
    AtomDeclaration,
    AtomDisposition,
    AtomDocument,
    AtomFact,
    AtomFieldClaim,
    AtomPlan,
    AtomPreview,
    AtomRole,
    AtomSlot,
    AtomState,
    SlotFormat,
)
from .semantics import (
    CONVENTIONAL,
    STANDARD_CLICK,
    click_is_deferred,
    displayed_value,
    select_role,
    select_states,
    value_is_displayed,
)

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.actions.control_raises import ControlRaise
    from computer_use.interaction.read.presentation.actions.menu_mechanisms import MenuMechanism
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import NodeView
    from computer_use.interaction.read.presentation.rendering.native_descriptions import NativeRoleAlias


@dataclass(frozen=True)
class AtomPolicy:
    """Captured display options available to the pure per-element planner."""

    compact: bool
    focused_ref: str | None
    captured_details: bool = False
    deferred_fields: frozenset[FactKey] = frozenset()
    role_aliases: Mapping[str, NativeRoleAlias] | None = None
    menu_mechanisms: tuple[MenuMechanism, ...] = ()
    control_raises: tuple[ControlRaise, ...] = ()
    selection_summaries: SelectionSummaryPlan | None = None


class _AtomBuilder:
    """Collect one element's explicit field destinations from immutable evidence."""

    def __init__(self, projection: ProjectionResult, node: NodeView, policy: AtomPolicy) -> None:
        self.projection = projection
        self.node = node
        self.ref: str = node["ref"]
        self.policy = policy
        self.slots: list[AtomSlot] = []
        self.destinations: list[AtomDisposition] = []
        self.claims: list[AtomFieldClaim] = []

    def claim(
        self,
        attribute: str,
        *,
        target_ref: str | None = None,
        kind: Literal["display", "equivalent", "shared", "continuation", "text-selection-summary"] = "display",
        source_ref: str | None = None,
    ) -> None:
        """Record normalized display fields independently of native provenance values."""
        node = self.node if source_ref is None or source_ref == self.ref else self.projection["nativeNodes"][source_ref]
        if attribute not in node:
            return
        target = target_ref or self.ref
        parts = (
            [part for part in node.get("textSources", ()) if part["field"] == attribute]
            if attribute in ("name", "description", "value") and kind != "continuation"
            else []
        )
        if parts:
            self.claims.extend(
                AtomFieldClaim(part["sourceRef"], part["attribute"], freeze(part["text"]), target, "equivalent")
                for part in parts
            )
        else:
            self.claims.append(AtomFieldClaim(node["ref"], attribute, freeze(node[attribute]), target, kind))

    def facts(self, attribute: str, *, source_ref: str | None = None) -> tuple[AtomFact, ...]:
        """Resolve provenance to exact native field occurrences, preserving repeated parts."""
        ref = source_ref or self.ref
        composed = self.projection.provenance.get((ref, attribute))
        if composed is not None:
            return tuple(AtomFact(key, freeze(part.value)) for part in composed.parts for key in part.facts)
        source = self.projection.source.native_nodes.get(ref, {})
        return (AtomFact(FactKey(SourceKey(ref), attribute), freeze(source[attribute])),) if attribute in source else ()

    def mark(
        self,
        facts: tuple[AtomFact, ...],
        slot: str,
        *,
        kind: Literal["inline", "equivalent", "details"] = "inline",
        target_ref: str | None = None,
    ) -> None:
        self.destinations.extend(AtomDisposition(fact, target_ref or self.ref, slot, kind) for fact in facts)

    def add(
        self,
        attribute: str,
        label: str | None,
        *,
        value: object = ...,
        format: SlotFormat = "text",
        placement: Literal["caption", "inline", "block"] = "inline",
        protection: str | None = None,
        source_attributes: tuple[str, ...] | None = None,
    ) -> None:
        if value is ...:
            value = self.node[attribute]
        facts = tuple(fact for key in (source_attributes or (attribute,)) for fact in self.facts(key))
        preview = None
        if self.policy.compact and self.policy.captured_details and protection is None and type(value) is str:
            if carrier := embedded_content(value):
                preview = AtomPreview("embedded", carrier.header, carrier.encoded_characters)
            elif attribute == "url":
                if value.lower().startswith(("data:", "webdoc:")):
                    scheme, _, rest = value.partition(":")
                    kind = rest.split(",", 1)[0] if scheme.lower() == "data" else ""
                    preview = AtomPreview("embedded-url", scheme + ":" + kind[:60])
                elif len(value) > 160:
                    preview = AtomPreview("url", value[:160])
        if preview is not None:
            format = "preview"
            placement = "inline" if placement == "block" else placement
        self.slots.append(
            AtomSlot(
                attribute,
                label,
                freeze(value),
                format,
                placement,
                facts,
                self.projection.provenance.get((self.ref, attribute)),
                protection,
                preview,
            )
        )
        self.mark(facts, attribute, kind="details" if preview is not None else "inline")
        if preview is None:
            for key in source_attributes or (attribute,):
                self.claim(key)

    def equivalent(self, attribute: str, destination: str, *, target_ref: str | None = None) -> None:
        slot = next((item for item in self.slots if item.key == destination), None)
        if target_ref is None and slot is not None and slot.preview is not None:
            self.mark(self.facts(attribute), destination, kind="details")
            return
        self.mark(self.facts(attribute), destination, kind="equivalent", target_ref=target_ref)
        self.claim(attribute, target_ref=target_ref, kind="equivalent")

    def deferred(self, attribute: str) -> bool:
        key = FactKey(SourceKey(self.ref), attribute)
        if self.policy.captured_details and key in self.policy.deferred_fields:
            self.mark(self.facts(attribute), attribute, kind="details")
            return True
        return False


def _coverage(node: NodeView) -> AtomCoverage:
    read = node.get("childrenRead", len(node.get("native", node).get("children", ())))
    total = node.get("childCount")
    reason = node.get("partialReason")
    if reason is None and node["role"] != "application" and total is not None and read < total:
        reason = "children not fully read"
    return AtomCoverage(
        reason,
        read,
        total,
        node.get("childrenStart"),
        node.get("childrenEnd"),
        node.get("nextChildOffset"),
        tuple(node.get("childOmissions", ())),
        unexpanded_menu(node),
    )


def _actions(node: NodeView, parts: tuple[NodeView, ...], policy: AtomPolicy) -> tuple[AtomAction, ...]:
    """Retain each selected action's original source and catalogue occurrence."""
    result: list[AtomAction] = []
    for owner in (node, *parts):
        selected = secondary_actions(owner)
        if policy.compact:
            selected = display_actions(
                owner, selected, menu_mechanisms=policy.menu_mechanisms, control_raises=policy.control_raises
            )
        # Enumerating the original sequence preserves duplicate native occurrences.
        identities = {id(action) for action in selected}
        for index, action in enumerate(owner.get("actions", ())):
            if id(action) not in identities:
                continue
            name = action["name"]
            label = action.get("description") or name
            ambiguous = any(
                other["name"] != name and (other["name"] == label or other.get("description") == label)
                for other in owner.get("actions", ())
            )
            result.append(AtomAction(owner["ref"], name, label, ambiguous, index, freeze(action)))
    return tuple(result)


def plan_atom(
    projection: ProjectionResult,
    node: NodeView,
    *,
    parent: NodeView | None,
    policy: AtomPolicy,
    folded_sources: tuple[NodeView, ...] = (),
) -> AtomPlan:
    """Prepare all ordinary fields, independent actions and coverage for one source.

    Args:
        projection: Compressed projection retaining its immutable original facts.
        node: Real retained element whose display fields are being organized.
        parent: Projected parent, used only for established inherited-state sharing.
        policy: Captured observation policy and proven native-detail routes.
        folded_sources: Sources already folded by compression into this exact owner.

    Returns:
        A frozen atomic plan with ordered typed slots and exact source destinations.
        It contains no complete node line, indentation or assembled tree text.
    """
    builder = _AtomBuilder(projection, node, policy)
    role_choice = select_role(node)
    alias = policy.role_aliases.get(node["ref"]) if policy.role_aliases else None
    if (
        alias is not None
        and node["role"] == alias.role
        and node.get("subrole") == alias.subrole
        and node.get("roleDescription") == alias.description.value
        and builder.deferred("roleDescription")
    ):
        role_facts = tuple(fact for field in ("role", "subrole") for fact in builder.facts(field))
        role = AtomRole(alias.label, role_facts)
    else:
        role_facts = tuple(fact for field in role_choice.attributes for fact in builder.facts(field))
        role = AtomRole(role_choice.label, role_facts, role_choice.source_description)
    builder.mark(role_facts, "role")
    for attribute in ("role", "subrole") if alias is not None and role.label == alias.label else role_choice.attributes:
        builder.claim(attribute)
    selected_states = select_states(node, compact=policy.compact, focused_ref=policy.focused_ref)
    states = tuple(
        AtomState(label, tuple(fact for field in attributes for fact in builder.facts(field)))
        for label, attributes in selected_states.labels
    )
    for state in states:
        builder.mark(state.facts, "state:" + state.label)
    for _, attributes in selected_states.labels:
        for attribute in attributes:
            builder.claim(attribute)
    for field in selected_states.equivalent_fields:
        builder.equivalent(field, "value" if field == "toggleState" else "state:disclosure")

    if node.get("name"):
        builder.add("name", None, placement="caption")
    for attribute, label in (
        ("readOnly", "Value read-only"),
        ("textReadOnly", "Text read-only"),
        ("rangeReadOnly", "Range read-only"),
        ("unsaved", "Unsaved"),
        ("invalid", "Invalid"),
    ):
        if attribute in node and node[attribute] is not True:
            if node[attribute] is False and policy.captured_details:
                builder.mark(builder.facts(attribute), attribute, kind="details")
            else:
                builder.add(attribute, label, format="literal", protection="state")
    if "errorRelations" in node:
        relations = node["errorRelations"]
        plain = all(
            item["state"] == "complete"
            and item["certainty"] == "confirmed"
            and item.get("enumeration", "enumerated") == "enumerated"
            and item.get("offset", 0) == 0
            and item.get("total", len(item["targets"])) == len(item["targets"])
            and tuple(item.get("positions", range(len(item["targets"])))) == tuple(range(len(item["targets"])))
            for item in relations
        )
        if plain:
            targets = tuple(ref for item in relations for ref in item["targets"])
            if not targets and policy.captured_details:
                builder.mark(builder.facts("errorRelations"), "errorRelations", kind="details")
            else:
                builder.add("errorRelations", "Error", value=targets, format="refs", protection="state")
        else:
            builder.add("errorRelations", "Error relation (unresolved)", format="literal", protection="state")
    if node.get("spatialPosition"):
        builder.add("spatialPosition", "Position")
    if node.get("visualLabelRef"):
        builder.add("visualLabelRef", "Visual label", value=(node["visualLabelRef"],), format="refs")
    for attribute, label in (("rowRange", "Row"), ("columnRange", "Column")):
        if attribute in node:
            builder.add(attribute, label, format="span")
    for attribute, label in (
        ("rowCount", "Table rows"),
        ("columnCount", "Table columns"),
        ("selectedChildCount", "Selected children"),
        ("index", "Index (0-based)"),
    ):
        if attribute in node:
            if (
                attribute == "index"
                and policy.compact
                and policy.captured_details
                and not index_has_collection_context(node["ref"], projection)
            ):
                builder.mark(builder.facts(attribute), attribute, kind="details")
                continue
            builder.add(attribute, label, format="number")
    if click_is_deferred(node):
        builder.add(
            "clickState", "Click", value="unknown (rechecked on click)", source_attributes=("enabled", "capabilities")
        )
    if node.get("editorOwnerRef"):
        builder.add("editorOwnerRef", "Editor for", value=(node["editorOwnerRef"],), format="refs")
    if "disclosureLevel" in node and node["disclosureLevel"] != node.get("outlineDepth"):
        builder.add("disclosureLevel", "Outline level", format="number")
    if node.get("disclosedBy"):
        builder.add("disclosedBy", "Parent row", value=(node["disclosedBy"],), format="refs")
    if node.get("incompleteDisclosureRelation"):
        builder.add("incompleteDisclosureRelation", "Parent row", value="outside this observation or unavailable")
    if node.get("placeholder"):
        builder.add("placeholder", "Placeholder")
    if "expanded" in node and "disclosing" in node and node["expanded"] != node["disclosing"]:
        builder.add(
            "disclosing",
            "Disclosure state (native reports disagree)",
            value="expanded" if node["disclosing"] else "collapsed",
        )
    caps = node.get("capabilities")
    if caps is not None and node.get("enabled") is not False and not node.get("blockedBy"):
        if caps.get("click", "unknown") != "available" and (
            node["role"] in (STANDARD_CLICK if policy.compact else CONVENTIONAL) or node.get("selectable")
        ):
            builder.add("clickState", "Click", value=caps.get("click", "unknown"), source_attributes=("capabilities",))
        if (
            node["role"] in ("textField", "textArea", "comboBox")
            and caps.get("typeText") != "available"
            and caps.get("replaceText") != "available"
        ):
            builder.add(
                "inputState", "Input", value=caps.get("typeText", "unknown"), source_attributes=("capabilities",)
            )
    if node["ref"] == policy.focused_ref and node.get("focused") is False:
        builder.add("focused", "Focus flag", value=False, format="literal", protection="focus")
    if node.get("description") and node["description"] != node.get("name"):
        if not builder.deferred("description"):
            builder.add("description", "Description")
    elif node.get("description") and node["description"] == node.get("name"):
        builder.equivalent("description", "name")
    if node.get("fullDescription"):
        builder.add("fullDescription", "Full description")
    for attribute, label in (("valueDescription", "Value description"), ("rangeDescription", "Range description")):
        if attribute not in node:
            continue
        if shared_description(node, attribute):
            # The complete typed declaration owns the whole group's disclosure.
            # Per-member deferral could conflict with its public fallback when a
            # different member lacks a valid same-capture detail destination.
            continue
        if not builder.deferred(attribute):
            builder.add(attribute, label)
    if node.get("localizedLandmarkType"):
        builder.add("localizedLandmarkType", "Landmark")
    show_value = value_is_displayed(node, policy.focused_ref)
    value = displayed_value(node)
    caption = next(
        (
            slot
            for slot in builder.slots
            if slot.key in ("name", "description")
            and "value" in node
            and type(slot.value) is type(node["value"])
            and slot.value == node["value"]
        ),
        None,
    )
    value_destination = None
    if show_value or "value" in node and caption is None:
        natural = (
            policy.compact
            and node["role"] == "staticText"
            and not node.get("name")
            and not node.get("description")
            and value is not None
        )
        input_value = (
            node["role"] in ("textField", "textArea", "comboBox")
            or any(caps.get(key) == "available" for key in ("typeText", "replaceText"))
            if caps
            else node["role"] in ("textField", "textArea", "comboBox")
        )
        builder.add(
            "value",
            None if natural else value_label(node),
            value=value,
            format="unavailable" if value is None else "text",
            placement="block" if isinstance(value, str) and any(c in value for c in "\r\n") else "inline",
            protection="input" if input_value else None,
        )
        value_destination = "value"
    elif caption is not None:
        # Equality with a projected field is insufficient: only an actual slot
        # can represent this value. Empty captions have no presentation slot.
        builder.equivalent("value", caption.key)
        value_destination = caption.key
    if node.get("labelTargets"):
        builder.add("labelTargets", "Labels for", format="refs")
    if node.get("incompleteLabelRelation"):
        builder.add("incompleteLabelRelation", "Label relation", value="partly outside this observation or unavailable")
    selection = display_selection(node) if policy.compact else node.get("selectionRange")
    if selection is not None:
        summarized = (
            policy.compact
            and policy.selection_summaries is not None
            and policy.selection_summaries.covers(node["ref"], selection)
        )
        if summarized:
            builder.mark(builder.facts("selectionRange"), "selection-summary", kind="equivalent")
            builder.claim("selectionRange", kind="text-selection-summary")
        else:
            builder.add("selectionRange", "Selection", value=selection, format="selection", protection="selection")
    if "shortcut" in node:
        builder.add("shortcut", "Shortcut")
    if "menuMark" in node:
        builder.add("menuMark", "Mark", value=node["menuMark"] if node["menuMark"] else "<none>")
    if node.get("selectedText") is not None:
        if not builder.deferred("selectedText"):
            builder.add("selectedText", "Selected text", protection="selection")
    if node.get("help"):
        same = next(
            (
                slot.key
                for slot in builder.slots
                if slot.key in ("name", "description", "value")
                and type(slot.value) is str
                and slot.value == node["help"]
            ),
            None,
        )
        if same is not None:
            builder.equivalent("help", same)
        else:
            builder.add("help", "Help")
    if node.get("url"):
        same = next(
            (
                slot.key
                for slot in builder.slots
                if slot.key in ("name", "description", "value", "help")
                and type(slot.value) is str
                and slot.value == node["url"]
            ),
            None,
        )
        if same is not None:
            builder.equivalent("url", same)
        else:
            builder.add("url", "URL")
    if "documentUrl" in node:
        builder.add("documentUrl", "Document")
    if "documentReports" in node:
        builder.add("documentReports", "Document reports (unresolved)", format="literal")
    if node.get("identifier"):
        if policy.compact and policy.captured_details:
            builder.mark(builder.facts("identifier"), "identifier", kind="details")
        else:
            builder.add("identifier", "ID")
    if node.get("blockedBy"):
        if parent is not None and tuple(node["blockedBy"]) == tuple(parent.get("blockedBy", ())):
            builder.equivalent("blockedBy", "blockedBy", target_ref=parent["ref"])
        else:
            builder.add("blockedBy", "Blocked by", format="refs", protection="blocked")
    for attribute, label in (
        ("documentText", "Document text"),
        ("controlValue", "Control value"),
        ("rangeValue", "Range value"),
    ):
        if attribute not in node:
            continue
        if value_destination is not None and (
            attribute in node.get("_numericValueAliases", ())
            and node[attribute] == node.get("value")
            and show_value
            or node[attribute] == node.get("value")
            and {attribute, "value"} <= set(node.get("_completeTextValues", ()))
        ):
            builder.equivalent(attribute, value_destination)
            continue
        builder.add(attribute, label, placement="block" if any(c in node[attribute] for c in "\r\n") else "inline")

    declarations = []
    for group in (*node.get("sharedDescriptions", ()), *node.get("sourceRoles", ())):
        facts = tuple(
            AtomFact(fact, freeze(projection.source.native_nodes[fact.source][fact.attribute])) for fact in group.facts
        )
        if policy.captured_details and all(fact.key in policy.deferred_fields for fact in facts):
            builder.mark(facts, group.attribute, kind="details")
            continue
        declarations.append(AtomDeclaration(group.attribute, freeze(group.value), facts))
        builder.mark(facts, group.attribute, kind="equivalent")
        for fact in facts:
            builder.claim(fact.key.attribute, source_ref=fact.key.source, kind="shared")
    actions = _actions(node, folded_sources, policy)
    return AtomPlan(
        node["ref"],
        parent["ref"] if parent is not None else None,
        role,
        states,
        tuple(builder.slots),
        actions,
        bool(node.get("actionsUnavailable")),
        _coverage(node),
        tuple(child["ref"] for child in node["children"]),
        freeze({key: value for key, value in node.items() if key not in ("children", "native")}),
        tuple(builder.destinations),
        tuple(declarations),
        tuple(builder.claims),
    )


def build_atoms(projection: ProjectionResult, branch: BranchFacts | None = None) -> AtomDocument:
    """Plan a captured projection's exact atomic content before choosing block layout.

    With BranchFacts, compact native details use the same verified capture routes
    as snapshot publication. Standalone plans retain those fields inline.
    """
    context = dict(projection.source.context)
    if branch is not None:
        context.update(branch.context)
    compact = bool(context.get("compact"))
    whole_view = (
        compact and context.get("publicApi") and context.get("shareDescriptions") and not context.get("subtreeRef")
    )
    aliases = None
    native_descriptions = {}
    deferred_fields: frozenset[FactKey] = frozenset()
    menu_mechanisms = ()
    control_raises = ()
    if branch is not None and whole_view:
        snapshot = branch.request.snapshot
        aliases = capture_common_role_aliases(snapshot, projection)
        native_descriptions = {
            **(capture_native_description_fields(snapshot, projection) or {}),
            **capture_code_descriptions(snapshot, projection),
            **capture_empty_selected_text_fields(snapshot, projection),
            **{alias.fact: alias.description for alias in aliases.values()},
        }
        deferred_fields = frozenset(native_descriptions)
        menu_mechanisms = capture_menu_mechanisms(snapshot, projection)
        control_raises = capture_control_raises(snapshot, projection)
    selections = (
        selection_summary_plan(
            branch.request.snapshot.facts, branch.request.snapshot.refs, roots=branch.request.snapshot.roots
        )
        if branch is not None and compact
        else None
    )
    policy = AtomPolicy(
        compact,
        context.get("focusedRef"),
        branch is not None,
        deferred_fields,
        aliases,
        menu_mechanisms,
        control_raises,
        selections,
    )
    folded: dict[str, list[NodeView]] = {}
    for ref, owner in projection["foldedInto"].items():
        folded.setdefault(owner, []).append(projection["nativeNodes"][ref])
    atoms: dict[str, AtomPlan] = {}
    order: list[str] = []
    pending = [(node, None) for node in reversed(projection["roots"])]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        if ref in atoms:
            raise ValueError("Atomic presentation requires one primary occurrence per source")
        atoms[ref] = plan_atom(
            projection, node, parent=parent, policy=policy, folded_sources=tuple(folded.get(ref, ()))
        )
        order.append(ref)
        pending.extend((child, node) for child in reversed(node["children"]))
    dispositions = tuple(disposition for ref in order for disposition in atoms[ref].dispositions)
    actual_deferred = {item.fact.key for item in dispositions if item.kind == "details"}
    return AtomDocument(
        projection.source,
        MappingProxyType(atoms),
        tuple(node["ref"] for node in projection["roots"]),
        tuple(order),
        policy.focused_ref,
        tuple(ref for ref in order if atoms[ref].source_fields.get("selected")),
        dispositions,
        tuple(claim for ref in order for claim in atoms[ref].field_claims),
        tuple(freeze(field.destination()) for key, field in native_descriptions.items() if key in actual_deferred),
        tuple(freeze(asdict(item)) for item in menu_mechanisms),
        tuple(freeze(asdict(item)) for item in control_raises),
    )
