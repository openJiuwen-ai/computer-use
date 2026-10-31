# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prove a native control caption covers ordered, inactive text components."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, fields
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.ir import FactKey, PresentedValue, SourceKey, ValuePart
from computer_use.interaction.read.presentation.policy import display_copy, independent_actions
from computer_use.interaction.read.presentation.compression.policy import protected_content
from computer_use.interaction.read.presentation.tree import disclosure, has_incomplete_children, VALUE_PREVIEW_LIMIT
from computer_use.protocol.observation import NumericValue, Structure
from computer_use.protocol.primitives import utf16_length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
    from computer_use.interaction.read.presentation.ir import ProjectionView, ViewNode
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.observation import Action, NativeNode, ReadIssue, TextInfo


@dataclass(frozen=True)
class PassiveCaptionSource:
    """Complete original value and input/selection evidence for one passive source.

    Attributes:
        native: Immutable native token associated with the original display reference.
        value: Complete control-value string, before formatting or abbreviation.
        input_actions: Original keyless input declarations, each explicitly unavailable.
        text: Original text-space and empty-selection metadata retained in captured details.
        ancestors: Unique original parent chain, stopping at its first unproven edge.
        reported_windows: Existing window declarations along that chain. A None target
            marks an incomplete declaration; an omitted entry means no reported target.
    """

    native: str
    value: str
    input_actions: tuple[Action, ...]
    text: TextInfo
    ancestors: tuple[str, ...]
    reported_windows: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class CaptionEquivalence:
    """A real native caption equal to ordered values on its passive descendants.

    The composition proves display equivalence without replacing the owner's field
    provenance. Each component keeps its own native identity and captured-detail fields.

    Attributes:
        owner: Retained control displaying its own original caption.
        scope: Original window or menu boundary shared by every component.
        display_field: Current name/description slot containing the native caption.
        anchor: Owner's exact original name or description source.
        composition: Ordered component values and facts with an explicit separator.
    """

    owner: SourceKey
    scope: SourceKey
    display_field: Literal["name", "description"]
    anchor: FactKey
    composition: PresentedValue


def _complete(
    issues: tuple[ReadIssue, ...],
    path: tuple[str, ...],
    *,
    absent: bool = False,
    unsupported: bool = False,
) -> bool:
    """Require local field evidence, accepting exact absence only when requested."""
    for issue in issues:
        local = issue.path[2:]
        if local[: len(path)] == path or path[: len(local)] == local:
            if (
                not absent
                or local != path
                or issue.state not in (("absent", "unsupported") if unsupported else ("absent",))
                or issue.continuation is not None
                or issue.alternatives
            ):
                return False
    return True


def _optional_record_complete(
    issues: tuple[ReadIssue, ...],
    name: str,
    record: NumericValue | Structure | None,
    record_type: type[NumericValue] | type[Structure],
) -> bool:
    """Check each optional field without treating known absence as a record failure."""
    names = {item.name for item in fields(record_type)}
    for issue in issues:
        local = issue.path[2:]
        if not local:
            return False
        if local[0] != name:
            continue
        missing = (
            record is None
            if len(local) == 1
            else (len(local) == 2 and local[1] in names and (record is None or getattr(record, local[1]) is None))
        )
        if (
            not missing
            or issue.state not in ("absent", "unsupported")
            or issue.continuation is not None
            or issue.alternatives
        ):
            return False
    return True


def _incidental_metadata(node: NativeNode, issues: tuple[ReadIssue, ...]) -> bool:
    """Keep only numeric bounds and disclosure fields already classified as details.

    A string-valued static text can report numeric bounds without a numeric value.
    The existing display policy decides whether its disclosure metadata is incidental;
    this proof does not remove any currently displayed level or disclosure state.
    """
    if not _optional_record_complete(issues, "structure", node.structure, Structure):
        return False
    if node.structure is not None and any(
        getattr(node.structure, item.name) is not None for item in fields(Structure) if item.name != "level"
    ):
        return False
    if node.states.expanded not in (None, "expanded", "collapsed"):
        return False
    display = dict(role="staticText")
    if node.states.expanded is not None:
        display["expanded"] = node.states.expanded == "expanded"
    if node.structure is not None and node.structure.level is not None:
        if type(node.structure.level) is not int:
            return False
        display["disclosureLevel"] = node.structure.level
    if disclosure(display) is not None or "disclosureLevel" in display_copy(display):
        return False
    if not _optional_record_complete(issues, "numeric_value", node.numeric_value, NumericValue):
        return False
    numeric = node.numeric_value
    if numeric is None:
        return True
    if any(
        getattr(numeric, item.name) is not None
        for item in fields(NumericValue)
        if item.name not in ("minimum", "maximum")
    ):
        return False
    bounds = tuple(value for value in (numeric.minimum, numeric.maximum) if value is not None)
    return all(type(value) is int or type(value) is float and math.isfinite(value) for value in bounds)


def _passive_native(node: NativeNode, issues: tuple[ReadIssue, ...]) -> tuple[Action, ...] | None:
    """Confirm unavailable input and only neutral, fully known text selections."""
    if (
        node.role != "static_text"
        or node.role_detail
        or node.actions is None
        or node.control_value is None
        or type(node.control_value.value) is not str
        or not node.control_value.value
        or not _complete(issues, ("role",))
        or not _complete(issues, ("role_detail",), absent=node.role_detail is None, unsupported=True)
        or not _complete(issues, ("control_value", "value"))
        or not _complete(issues, ("actions",))
    ):
        return None
    for name in ("name", "description", "help", "identifier", "placeholder", "url"):
        value = getattr(node, name)
        if value not in (None, "") or not _complete(issues, (name,), absent=value is None, unsupported=True):
            return None
    if (
        node.control_value.description not in (None, "")
        or not _complete(
            issues, ("control_value", "description"), absent=node.control_value.description is None, unsupported=True
        )
        or node.control_value.read_only is not None
        or not _complete(issues, ("control_value", "read_only"), absent=True, unsupported=True)
    ):
        return None
    states = node.states
    for name, expected in (
        ("enabled", True),
        ("focused", False),
        ("selected", False),
        ("busy", False),
        ("modal", False),
    ):
        if getattr(states, name) is not expected or not _complete(issues, ("states", name)):
            return None
    if states.checked is not None or states.window_state is not None:
        return None
    optional_states = {
        "editable": False,
        "focusable": False,
        "selectable": False,
        "protected": False,
        "has_popup": False,
        "active": False,
        "main_window": False,
        "visible": True,
        "showing": True,
        "offscreen": False,
        "virtualized": False,
    }
    for name, neutral in optional_states.items():
        actual = getattr(states, name)
        if actual is not None and actual is not neutral:
            return None
    for name in (*optional_states, "checked", "expanded", "window_state"):
        if not _complete(issues, ("states", name), absent=getattr(states, name) is None, unsupported=True):
            return None
    if not _incidental_metadata(node, issues):
        return None
    if any(
        relation.targets
        for relation in node.relations.values()
        if relation.kind not in ("children", "parent", "window")
    ):
        return None
    for kind in ("labels", "labelled_by"):
        relations = [(key, relation) for key, relation in node.relations.items() if relation.kind == kind]
        if len(relations) > 1:
            return None
        if relations:
            key, relation = relations[0]
            if (
                relation.targets
                or relation.total != 0
                or relation.offset not in (None, 0)
                or relation.positions not in (None, ())
                or relation.evidence not in ("reported", "verified")
                or relation.enumeration != "enumerated"
                or not _complete(issues, ("relations", key), absent=True)
            ):
                return None
        elif not _complete(issues, ("relations", kind)):
            return None
    inputs = []
    for kind, target in (
        ("focus", None),
        ("set_value", ("control_value", "value")),
        ("replace_selection", ("text", "selections")),
    ):
        matches = tuple(action for action in node.actions if action.kind == kind)
        if (
            len(matches) != 1
            or matches[0].key is not None
            or matches[0].available is not False
            or matches[0].target != target
        ):
            return None
        inputs.append(matches[0])
    if any(
        action.kind not in ("focus", "set_value", "replace_selection", "show_menu", "scroll_into_view")
        for action in node.actions
    ):
        return None
    text = node.text
    if (
        text is None
        or text.content_field != ("control_value", "value")
        or text.content is not None
        or text.unit != "utf16_code_unit"
        or text.selections is None
        or text.read_only is not None
        or not _complete(issues, ("text", "read_only"), absent=True, unsupported=True)
        or not _complete(issues, ("text", "selections"))
        or text.selected_text not in (None, "")
        or not _complete(issues, ("text", "selected_text"), absent=text.selected_text is None, unsupported=True)
        or text.caret is not None
        or text.caret_active is not None
        and text.caret_active is not False
        or any(
            getattr(text, name) is not None
            for name in ("runs", "embedded", "visible_ranges", "line_ranges", "supported_selection")
        )
        or text.character_count is not None
        and (type(text.character_count) is not int or text.character_count != utf16_length(node.control_value.value))
    ):
        return None
    for item in fields(text):
        if not _complete(issues, ("text", item.name), absent=getattr(text, item.name) is None, unsupported=True):
            return None
    for span in text.selections:
        if (
            type(span.start) is not int
            or type(span.end) is not int
            or span.start != 0
            or span.end != 0
            or span.collapsed is not True
            or span.text not in (None, "")
            or span.geometry
            or span.attributes
            or span.read_ref is not None
        ):
            return None
    return tuple(inputs)


def _window_targets(
    node: NativeNode,
    issues: tuple[ReadIssue, ...],
    aliases: Mapping[str, str],
) -> tuple[str, ...] | None:
    """Read an optional complete window declaration without querying missing data."""
    relations = [(key, relation) for key, relation in node.relations.items() if relation.kind == "window"]
    if not relations:
        return () if _complete(issues, ("relations", "window"), absent=True) else None
    if len(relations) != 1:
        return None
    key, relation = relations[0]
    size = len(relation.targets)
    if (
        size > 1
        or type(relation.total) is not int
        or relation.total != size
        or relation.offset not in (None, 0)
        or relation.positions not in (None, tuple(range(size)))
        or relation.evidence not in ("reported", "verified")
        or relation.enumeration != "enumerated"
        or not _complete(issues, ("relations", key), absent=size == 0)
        or any(target not in aliases for target in relation.targets)
    ):
        return None
    return tuple(aliases[target] for target in relation.targets)


def capture_caption_sources(snapshot: SnapshotFacts) -> Mapping[str, PassiveCaptionSource]:
    """Capture narrow native evidence without changing normalization or existing guards."""
    if any(not issue.path or issue.path == ("nodes",) and issue.state != "partial" for issue in snapshot.native.issues):
        return MappingProxyType({})
    issues: dict[str, list[ReadIssue]] = {}
    for issue in snapshot.native.issues:
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues.setdefault(issue.path[1], []).append(issue)
    counts = Counter(snapshot.refs.values())
    aliases = {token: ref for ref, token in snapshot.refs.items() if counts[token] == 1}
    incoming: dict[str, list[str]] = {}
    for token, node in snapshot.native.nodes.items():
        for relation in node.relations.values():
            if relation.kind == "children":
                for child in relation.targets:
                    incoming.setdefault(child, []).append(token)
    parents = {}
    inconsistent_parents: set[str] = set()
    windows: dict[str, tuple[str, ...] | None] = {}
    for token, node in snapshot.native.nodes.items():
        candidates = incoming.get(token, ())
        declared = [(key, relation) for key, relation in node.relations.items() if relation.kind == "parent"]
        local = tuple(issues.get(token, ()))
        if token in aliases:
            windows[aliases[token]] = _window_targets(node, local, aliases)
        if token not in aliases:
            continue
        if len(candidates) > 1 or len(declared) > 1:
            inconsistent_parents.add(aliases[token])
            continue
        if len(candidates) != 1 or candidates[0] not in aliases:
            continue
        if declared:
            key, relation = declared[0]
            if (
                relation.targets != (candidates[0],)
                or type(relation.total) is not int
                or relation.total != 1
                or relation.offset not in (None, 0)
                or relation.positions not in (None, (0,))
                or relation.evidence not in ("reported", "verified")
                or relation.enumeration != "enumerated"
                or not _complete(local, ("relations", key))
            ):
                inconsistent_parents.add(aliases[token])
                continue
        elif not _complete(local, ("relations", "parent")):
            inconsistent_parents.add(aliases[token])
            continue
        parents[aliases[token]] = aliases[candidates[0]]
    result = {}
    for ref, token in snapshot.refs.items():
        node = snapshot.native.nodes.get(token)
        if node is None or counts[token] != 1 or snapshot.value_fields.get(ref) != "control_value.value":
            continue
        inputs = _passive_native(node, tuple(issues.get(token, ())))
        if inputs is not None:
            if ref in inconsistent_parents:
                continue
            current, ancestors = ref, []
            while current in parents and parents[current] not in (ref, *ancestors):
                parent = parents[current]
                parent_node = snapshot.native.nodes[snapshot.refs[parent]]
                boundary = parent_node.role in ("window", "dialog", "alert")
                if parent in inconsistent_parents and not boundary:
                    break
                ancestors.append(parent)
                if boundary:
                    break
                current = parent
            path = (ref, *ancestors)
            declarations = tuple(
                (current, target)
                for current in path
                for target in ((None,) if windows[current] is None else windows[current])
            )
            result[ref] = PassiveCaptionSource(
                token, node.control_value.value, inputs, node.text, tuple(ancestors), declarations
            )
    return MappingProxyType(result)


def _complete_text(value: object) -> bool:
    """Exclude text forms whose default renderer can abbreviate or reinterpret them."""
    return (
        type(value) is str
        and bool(value)
        and utf16_length(value) <= VALUE_PREVIEW_LIMIT
        and ";base64," not in value.lower()
        and not any(char in value for char in "\r\n\x85\u2028\u2029")
    )


def caption_equivalence(
    view: ProjectionView,
    owner: ViewNode,
    evidence: CompressionEvidence,
    visible: Mapping[str, ViewNode],
    referenced_labels: frozenset[str],
) -> CaptionEquivalence | None:
    """Prove exact caption composition within complete original control containment.

    Only already-elided anonymous wrappers may intervene. Images remain independent;
    other retained boundaries, additional text fields and uncertain inputs keep the tree.
    """
    ref = owner["ref"]
    original = view.native_nodes[ref]
    if (
        owner["role"] not in ("link", "button")
        or original["role"] != owner["role"]
        or owner.get("capabilities", {}).get("click") != "available"
        or owner.get("actionsUnavailable")
        or owner.get("incompleteLabelRelation")
        or has_incomplete_children(owner)
        or has_incomplete_children(original)
        or ref not in evidence.complete_children
    ):
        return None
    field: Literal["name", "description"] = "name" if owner.get("name") else "description"
    caption = view.field_value(ref, field)
    if (
        caption is None
        or len(caption.parts) != 1
        or len(caption.parts[0].facts) != 1
        or not _complete_text(caption.value)
    ):
        return None
    anchor = caption.parts[0].facts[0]
    if (
        anchor.source != ref
        or anchor.attribute not in ("name", "description")
        or original.get(anchor.attribute) != caption.value
        or (ref, anchor.attribute) not in evidence.complete_text
        or any(original.get(name) not in (None, "", caption.value) for name in ("name", "description"))
    ):
        return None
    scope, parts, visited = view.scopes[ref], [], set()

    def visit(current: str, parent: str) -> bool:
        if (
            current in visited
            or view.parents.get(current) != parent
            or view.scopes.get(current) != scope
            or current in evidence.incomplete_children
        ):
            return False
        visited.add(current)
        native = view.native_nodes.get(current)
        if native is None or has_incomplete_children(native):
            return False
        node = visible.get(current)
        if native["role"] == "staticText":
            source = evidence.caption_sources.get(current)
            if (
                node is None
                or source is None
                or node["children"]
                or has_incomplete_children(node)
                or ref not in source.ancestors
                or view.keep(node)
                or current == view.context.get("focusedRef")
                or node.get("valueIsNumber")
                or node.get("valueKind") == "document"
                or node.get("blockedBy", ()) != owner.get("blockedBy", ())
                or (current, "value") not in evidence.complete_text
                or not _complete_text(source.value)
                or any(
                    node.get(key)
                    for key in (
                        "name",
                        "description",
                        "help",
                        "url",
                        "identifier",
                        "titleRef",
                        "labelTargets",
                        "linkedLabel",
                        "incompleteLabelRelation",
                        "preserveGroup",
                        "subrole",
                        "placeholder",
                        "documentUrl",
                        "documentText",
                        "controlValue",
                        "rangeValue",
                        "valueExpected",
                        "headingLevel",
                        "landmarkType",
                        "fullDescription",
                        "shortcut",
                        "menuMark",
                        "toggleState",
                        "disclosedBy",
                        "incompleteDisclosureRelation",
                    )
                )
                or current in referenced_labels
                or independent_actions(node)
            ):
                return False
            local_path = frozenset((current, *source.ancestors[: source.ancestors.index(ref) + 1]))
            if any(target != scope for member, target in source.reported_windows if member in local_path):
                return False
            # This local view exempts only proven inactive empty selection metadata.
            # Every other established content/state guard continues to apply.
            protected = dict(node)
            if protected.get("selectionRange") == {"location": 0, "length": 0}:
                protected.pop("selectionRange")
            if protected.get("selectedText") == "":
                protected.pop("selectedText")
            # SDK unknown describes an undeclared route, independently of the complete
            # native input declarations above. It remains unknown in the source record.
            if (
                protected_content(protected)
                or any(
                    node.get(key)
                    for key in (
                        "focused",
                        "selected",
                        "pressable",
                        "focusable",
                        "selectable",
                        "expandable",
                        "submenu",
                        "modal",
                    )
                )
                or any(value == "available" for value in node.get("capabilities", {}).values())
            ):
                return False
            value = view.field_value(current, "value")
            fact = FactKey(SourceKey(current), "value")
            if (
                value is None
                or value.value != source.value
                or len(value.parts) != 1
                or value.parts[0].facts != (fact,)
                or native.get("value") != source.value
            ):
                return False
            parts.append(ValuePart(source.value, (fact,)))
            return True
        children = evidence.complete_children.get(current)
        if native["role"] == "image":
            return node is not None and not native["children"] and not independent_actions(native)
        if (
            node is not None
            or native["role"] != "group"
            or children is None
            or any(native.get(key) for key in ("name", "description", "value", "help", "url", "titleRef"))
            or independent_actions(native)
        ):
            return False
        return all(visit(child, current) for child in children)

    if not all(visit(child, ref) for child in evidence.complete_children[ref]) or len(parts) < 2:
        return None
    composition = PresentedValue(tuple(parts), " ")
    if composition.value != caption.value:
        return None
    return CaptionEquivalence(SourceKey(ref), SourceKey(scope), field, anchor, composition)
