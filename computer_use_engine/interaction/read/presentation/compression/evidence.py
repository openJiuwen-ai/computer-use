# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Narrow source proofs for interpreting conservative display boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from dataclasses import fields as dataclass_fields
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.protocol.observation import FieldPath, NativeNode, ReadIssue, RelationKind
from computer_use.interaction.read.presentation.compression.caption_equivalence import (
    PassiveCaptionSource,
    capture_caption_sources,
)

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionView, ViewNode
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.records import FactNode


@dataclass(frozen=True)
class CompressionEvidence:
    """Source proofs that leave the normalized preservation flags unchanged.

    Attributes:
        complete_text: Exact source reference/field pairs with complete string values.
        complete_descriptions: Exact source/field pairs with complete empty descriptions.
        provider_wrappers: Canonical groups whose sole extra boundary fact is a provider
            role detail, with one complete child and no independent native semantics.
        unlabelled_groups: Groups with complete empty native label relationships and no
            incoming label reference; derived captions may be reconsidered independently.
        complete_children: Complete ordered native child collections, expressed in public
            references for checking the containment edges used by caption deduplication.
        incomplete_children: Declared child collections without a complete captured range,
            including incomplete collections on otherwise leaf-like text sources.
        caption_sources: Complete passive text/input evidence used only by native-caption
            composition; existing compression guards do not consume this map.
        concrete_inputs: Explicit editor/read-write text semantics independent of generic
            native setter declarations and public route availability.
        semantic_regions: Native structure or business content not fully represented by
            the legacy display-node fields.
        contested_sources: Endpoints of local, known parent/window ownership conflicts.
        incomplete_content: Sources with unresolved reads of their own visible caption
            or primary value, independent of unrelated native getter diagnostics.
        splice_conflicts: Endpoints of multiple captured child owners or explicit window
            contradictions; reverse parent declarations alone do not prevent a splice.
        control_value_lines: References whose complete primary control value is the exact
            native text channel, with a known text unit and no separate document content.
    """

    complete_text: frozenset[tuple[str, str]] = frozenset()
    provider_wrappers: frozenset[str] = frozenset()
    complete_descriptions: frozenset[tuple[str, str]] = frozenset()
    unlabelled_groups: frozenset[str] = frozenset()
    complete_children: Mapping[str, tuple[str, ...]] = dataclass_field(default_factory=dict)
    incomplete_children: frozenset[str] = frozenset()
    caption_sources: Mapping[str, PassiveCaptionSource] = dataclass_field(default_factory=dict)
    concrete_inputs: frozenset[str] = frozenset()
    semantic_regions: frozenset[str] = frozenset()
    contested_sources: frozenset[str] = frozenset()
    incomplete_content: frozenset[str] = frozenset()
    splice_conflicts: frozenset[str] = frozenset()
    control_value_lines: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "complete_children",
            MappingProxyType({ref: tuple(children) for ref, children in self.complete_children.items()}),
        )
        object.__setattr__(self, "caption_sources", MappingProxyType(dict(self.caption_sources)))
        for name in (
            "concrete_inputs",
            "semantic_regions",
            "contested_sources",
            "incomplete_content",
            "splice_conflicts",
            "control_value_lines",
        ):
            object.__setattr__(self, name, frozenset(getattr(self, name)))


def _control_value_lines(
    snapshot: SnapshotFacts, native_nodes: Mapping[str, FactNode], issues: Mapping[str, list[ReadIssue]]
) -> frozenset[str]:
    """Identify complete control values whose native text explicitly aliases that field."""
    if any(not item.path or item.path == ("nodes",) and item.state != "partial" for item in snapshot.native.issues):
        return frozenset()
    result = set()
    for ref, token in snapshot.refs.items():
        node = snapshot.native.nodes.get(token)
        source = snapshot.fields.get((ref, "control_value.value"))
        if node is None or node.control_value is None:
            continue
        # pylint: disable-next=unidiomatic-typecheck
        if type(node.control_value.value) is not str or not node.control_value.value:
            continue
        # pylint: disable-next=unidiomatic-typecheck
        if node.text is None or type(node.text.source) is not str or not node.text.source:
            continue
        if (
            node.text.content_field != ("control_value", "value")
            or node.text.unit not in ("utf16_code_unit", "unicode_codepoint")
            or node.text.content is not None
        ):
            continue
        if snapshot.value_fields.get(ref) != "control_value.value" or source is None:
            continue
        if (
            source.get("field") != "value"
            or tuple(source.get("path", ())) != ("control_value", "value")
            or source.get("normalized") != node.control_value.value
        ):
            continue
        if native_nodes.get(token, {}).get("values", {}).get("control_value.value") != node.control_value.value:
            continue
        paths = (("role",), ("control_value", "value"), ("text", "source"), ("text", "unit"), ("text", "content_field"))
        incomplete = any(
            any(item.path[2:][: len(path)] == path or path[: len(item.path[2:])] == item.path[2:] for path in paths)
            for item in issues.get(token, ())
        )
        unknown_content = any(
            item.path[2:][:2] == ("text", "content")
            and (item.state not in ("absent", "unsupported") or item.continuation or item.alternatives)
            for item in issues.get(token, ())
        )
        if not incomplete and not unknown_content:
            result.add(ref)
    return frozenset(result)


def _incomplete_content(issues: tuple[ReadIssue, ...]) -> bool:
    """Identify only read gaps in the visible fields used to establish anonymity."""
    fields = (
        ("role",),
        ("name",),
        ("description",),
        ("control_value", "value"),
        ("numeric_value", "value"),
        ("text", "content"),
    )
    return any(
        (issue.state not in ("absent", "unsupported") or issue.continuation or issue.alternatives)
        and any(issue.path[2:][: len(path)] == path or path[: len(issue.path[2:])] == issue.path[2:] for path in fields)
        for issue in issues
    )


def _concrete_input(node: NativeNode) -> bool:
    """Recognize actual editor semantics rather than a generic writable AX attribute."""
    return (
        node.role in ("text_field", "text_area", "combo_box", "document")
        or node.states.editable is True
        or node.control_value is not None
        and node.control_value.read_only is False
        or node.numeric_value is not None
        and node.numeric_value.read_only is False
        or node.text is not None
        and node.text.read_only is False
    )


def _semantic_region(node: NativeNode) -> bool:
    """Retain explicit collection, viewport and business semantics on provider groups."""
    structure = node.structure
    if structure is not None:
        collection_fields = (
            "heading_level",
            "landmark",
            "landmark_description",
            "index",
            "set_size",
            "selected_count",
            "row_count",
            "column_count",
            "row_index",
            "column_index",
            "row_span",
            "column_span",
            "collection_mode",
        )
        if any(getattr(structure, name) is not None for name in collection_fields):
            return True
        if structure.level is not None and structure.level != 0:
            return True
        if structure.can_select_multiple is True or structure.selection_required is True:
            return True
        for axis in (structure.scroll_x, structure.scroll_y):
            if axis is not None and (axis.scrollable is True or axis.position not in (None, 0)):
                return True
    text = node.text
    active_text = text is not None and (
        text.caret_active is True
        or text.selections is not None
        and (
            len(text.selections) > 1
            or any(
                span.collapsed is False or span.start not in (None, 0) or span.end not in (None, 0)
                for span in text.selections
            )
        )
    )
    return (
        node.states.protected is True
        or node.states.offscreen is True
        or node.states.active is True
        or node.states.has_popup is True
        or node.states.virtualized is True
        or node.states.expanded == "partial"
        or active_text
        or any(
            item.key in ("item_status", "item_type")
            and item.value is not None
            # pylint: disable-next=unidiomatic-typecheck
            and not (type(item.value) is str and item.value == "")
            for item in node.supplements
        )
    )


def _reported_owner(node: NativeNode, kind: str, issues: tuple[ReadIssue, ...]) -> str | None:
    """Read one complete owner declaration without inferring one from missing getters."""
    matches = [(key, relation) for key, relation in node.relations.items() if relation.kind == kind]
    if len(matches) != 1:
        return None
    key, relation = matches[0]
    if len(relation.targets) != 1 or relation.total not in (None, 1):
        return None
    if relation.offset not in (None, 0) or relation.positions not in (None, (0,)):
        return None
    if relation.enumeration != "enumerated" or relation.evidence == "candidate":
        return None
    if not _relation_complete(key, issues, empty=False):
        return None
    return relation.targets[0]


def _source_conflicts(
    snapshot: SnapshotFacts,
    reference: Mapping[str, str],
    children: Mapping[str, tuple[str, ...]],
    issues: Mapping[str, list[ReadIssue]],
) -> tuple[frozenset[str], frozenset[str]]:
    """Separate content ownership conflicts from conflicts in pure display splices."""
    native = snapshot.native.nodes
    scopes: dict[str, str] = {}
    pending = [(root, None) for root in snapshot.roots]
    while pending:
        node, scope = pending.pop()
        if node["role"] in ("window", "sheet"):
            scope = snapshot.refs[node["ref"]]
        if scope is not None:
            scopes[node["ref"]] = scope
        pending.extend((child, scope) for child in node["children"])
    parent_declarations = {
        reference[token]: _reported_owner(node, "parent", tuple(issues.get(token, ())))
        for token, node in native.items()
        if token in reference
    }
    window_declarations = {
        reference[token]: _reported_owner(node, "window", tuple(issues.get(token, ())))
        for token, node in native.items()
        if token in reference
    }
    incoming: dict[str, set[str]] = {}
    # A known edge is still an edge when the provider omitted its full child count.
    # Completeness constrains a splice; it must not erase a second reported parent.
    for token, node in native.items():
        if token not in reference:
            continue
        for relation in node.relations.values():
            if relation.kind == "children" and relation.evidence != "candidate":
                for target in relation.targets:
                    if target in reference:
                        incoming.setdefault(reference[target], set()).add(reference[token])
    contested: set[str] = set()
    splice_conflicts: set[str] = set()
    for child, parents in incoming.items():
        if len(parents) > 1:
            contested.add(child)
            contested.update(parents)
            splice_conflicts.add(child)
            splice_conflicts.update(parents)
    for parent, targets in children.items():
        parent_token = snapshot.refs[parent]
        for child in targets:
            declared_parent = parent_declarations.get(child)
            declared_window = window_declarations.get(child)
            expected_window = scopes.get(child)
            window_conflict = (
                declared_window is not None and expected_window is not None and declared_window != expected_window
            )
            ownership_conflict = len(incoming.get(child, ())) > 1
            if not ownership_conflict and declared_parent is not None:
                ownership_conflict = declared_parent != parent_token
            if ownership_conflict or window_conflict:
                contested.update((parent, child))
            if window_conflict:
                splice_conflicts.update((parent, child))
    for ref, declaration in window_declarations.items():
        if declaration is not None and ref in scopes and declaration != scopes[ref]:
            contested.add(ref)
            splice_conflicts.add(ref)
            splice_conflicts.update(incoming.get(ref, ()))
    return frozenset(contested), frozenset(splice_conflicts)


def _relation_complete(key: str, issues: tuple[ReadIssue, ...], *, empty: bool) -> bool:
    """Accept exact absence only when the relation separately proves an empty set."""
    for issue in issues:
        path = issue.path[2:]
        if path and path != ("relations",) and path[:2] != ("relations", key):
            continue
        if not empty or path != ("relations", key) or issue.state != "absent":
            return False
        if issue.continuation is not None or issue.alternatives:
            return False
    return True


def _unlabelled_group(node: NativeNode, issues: tuple[ReadIssue, ...]) -> bool:
    """Confirm empty label relations, including an exact reported absence.

    An absent relation can confirm a fully enumerated empty collection. Errors,
    partial reads and diagnostics on a wider or narrower path leave it unknown.
    """
    if node.role != "group":
        return False
    for kind in ("labelled_by", "labels"):
        relations = [(key, relation) for key, relation in node.relations.items() if relation.kind == kind]
        if len(relations) != 1:
            return False
        key, relation = relations[0]
        if relation.targets or relation.total != 0:
            return False
        if relation.offset not in (None, 0) or relation.positions not in (None, ()):
            return False
        if relation.enumeration != "enumerated" or relation.evidence == "candidate":
            return False
        if not _relation_complete(key, issues, empty=True):
            return False
    return True


def _complete_children(node: NativeNode, issues: tuple[ReadIssue, ...]) -> tuple[str, ...] | None:
    """Keep an ordered child collection only when its entire native range was read."""
    relations = [(key, relation) for key, relation in node.relations.items() if relation.kind == "children"]
    if len(relations) != 1:
        return None
    key, relation = relations[0]
    size = len(relation.targets)
    if type(relation.total) is not int or relation.total != size:
        return None
    if relation.offset not in (None, 0) or len(set(relation.targets)) != size:
        return None
    if relation.positions is not None and relation.positions != tuple(range(size)):
        return None
    if (
        relation.enumeration != "enumerated"
        or relation.evidence == "candidate"
        or not _relation_complete(key, issues, empty=size == 0)
    ):
        return None
    return relation.targets


def _provider_wrapper(node: NativeNode, issues: tuple[ReadIssue, ...], diagnostics: set[FieldPath]) -> bool:
    """Recognize a complete, inactive one-child wrapper from native facts."""
    if node.role != "group" or node.role_detail is not None:
        return False
    semantic_fields = (
        "name",
        "description",
        "help",
        "identifier",
        "placeholder",
        "url",
        "control_value",
        "numeric_value",
        "text",
        "structure",
        "shortcuts",
    )
    if any(getattr(node, key) is not None for key in semantic_fields):
        return False
    if (
        node.actions is None
        or any(
            action.key is not None
            or action.available is not False
            or action.kind not in ("focus", "set_value", "replace_selection")
            or action.target
            != {
                "focus": None,
                "set_value": ("control_value", "value"),
                "replace_selection": ("text", "selections"),
            }.get(action.kind)
            or action.label
            or action.description
            or action.shortcuts
            for action in node.actions
        )
    ):
        return False
    if any(
        issue.state not in ("unsupported", "absent") or issue.continuation or issue.alternatives for issue in issues
    ):
        return False
    if any(geometry.valid is False for geometry in node.geometry.values()):
        return False
    for part in dataclass_fields(node.states):
        value = getattr(node.states, part.name)
        if value is not None and not (
            part.name == "focused" and value is False or part.name == "enabled" and value is True
        ):
            return False
    supplements = [item for index, item in enumerate(node.supplements) if ("supplements", index) not in diagnostics]
    if len(supplements) != 1 or supplements[0].key != "provider.role_detail":
        return False
    if not isinstance(supplements[0].value, str) or not supplements[0].value:
        return False
    children = [(key, relation) for key, relation in node.relations.items() if relation.kind == "children"]
    if len(children) != 1:
        return False
    key, child = children[0]
    if len(child.targets) != 1 or child.total not in (None, 1):
        return False
    if child.offset not in (None, 0) or child.positions not in (None, (0,)):
        return False
    if child.enumeration != "enumerated" or child.evidence == "candidate":
        return False
    if any(issue.path[2:4] == ("relations", key) for issue in issues):
        return False
    for relation_key, relation in node.relations.items():
        if relation.kind == "children":
            continue
        unsupported = any(
            issue.path[2:] == ("relations", relation_key) and issue.state in ("unsupported", "absent")
            for issue in issues
        )
        if relation.kind in ("parent", "window") and relation.targets:
            if len(relation.targets) != 1 or relation.total not in (None, 1):
                return False
            if relation.evidence == "candidate" or relation.enumeration != "enumerated":
                return False
            if relation.offset not in (None, 0) or relation.positions not in (None, (0,)):
                return False
            if any(issue.path[2:4] == ("relations", relation_key) for issue in issues):
                return False
            continue
        if relation.targets or not unsupported:
            return False
    return True


def _relation_targets(nodes: Iterable[NativeNode], includes: Callable[[RelationKind], bool]) -> Iterator[str]:
    for node in nodes:
        for relation in node.relations.values():
            if includes(relation.kind):
                yield from relation.targets


def capture_evidence(snapshot: SnapshotFacts, native_nodes: Mapping[str, FactNode]) -> CompressionEvidence:
    """Capture only completeness and wrapper proofs needed by compression.

    Provider diagnostics retain their existing adapter classification. Unknown fields,
    partial collections, read errors and independent relations prevent wrapper removal.
    """
    complete = set()
    descriptions = set()
    issues: dict[str, list[ReadIssue]] = {}
    for issue in snapshot.native.issues:
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues.setdefault(issue.path[1], []).append(issue)
    for (ref, name), source in snapshot.fields.items():
        field = source.get("field")
        token = snapshot.refs.get(ref)
        native = native_nodes.get(token) if token is not None else None
        if field in ("name", "description", "value") and native is not None and name not in native["fieldStatus"]:
            if isinstance(native["values"].get(name), str):
                complete.add((ref, field))
        description_path = {
            "valueDescription": ("control_value", "description"),
            "rangeDescription": ("numeric_value", "description"),
        }.get(field)
        original = snapshot.native.nodes.get(token)
        description = (
            getattr(original, description_path[0], None)
            if description_path is not None and original is not None
            else None
        )
        complete_empty_description = description_path is not None and description is not None and native is not None
        complete_empty_description = (
            complete_empty_description
            # pylint: disable-next=unidiomatic-typecheck
            and type(description.description) is str
            and bool(description.description == "")
            and bool(tuple(source.get("path", ())) == description_path)
        )
        complete_empty_description = (
            complete_empty_description
            # pylint: disable-next=unidiomatic-typecheck
            and type(native["values"].get(name)) is str
        )
        if (
            complete_empty_description
            and native["values"][name] == ""
            and not any(
            key == name or key.startswith(name + ".") or name.startswith(key + ".") for key in native["fieldStatus"]
            )
        ):
            path = ("nodes", token, *description_path)
            if not any(
                path[: len(issue.path)] == issue.path or issue.path[: len(path)] == path
                for issue in issues.get(token, ())
            ):
                descriptions.add((ref, field))
    related = set(
        _relation_targets(snapshot.native.nodes.values(), lambda kind: kind not in ("children", "parent", "window"))
    )
    parents = Counter(_relation_targets(snapshot.native.nodes.values(), lambda kind: kind == "children"))
    diagnostics = snapshot.collection.diagnostic_fields if snapshot.collection is not None else {}
    wrappers = set()
    for ref, token in snapshot.refs.items():
        if token in related or parents[token] > 1 or token not in snapshot.native.nodes:
            continue
        if _provider_wrapper(
            snapshot.native.nodes[token], tuple(issues.get(token, ())), set(diagnostics.get(token, ()))
        ):
            wrappers.add(ref)
    label_targets = set(
        _relation_targets(snapshot.native.nodes.values(), lambda kind: kind in ("labelled_by", "labels"))
    )
    unlabelled = set()
    for ref, token in snapshot.refs.items():
        if token in label_targets or token not in snapshot.native.nodes:
            continue
        if _unlabelled_group(snapshot.native.nodes[token], tuple(issues.get(token, ()))):
            unlabelled.add(ref)
    counts = Counter(snapshot.refs.values())
    reference = {token: ref for ref, token in snapshot.refs.items() if counts[token] == 1}
    children = {}
    incomplete_children = set()
    for token, ref in reference.items():
        if token not in snapshot.native.nodes:
            continue
        targets = _complete_children(snapshot.native.nodes[token], tuple(issues.get(token, ())))
        if targets is not None and all(target in reference and target in snapshot.native.nodes for target in targets):
            children[ref] = tuple(reference[target] for target in targets)
        elif any(relation.kind == "children" for relation in snapshot.native.nodes[token].relations.values()) or any(
            not issue.path[2:] or issue.path[2:] == ("relations",) or issue.path[2:4] == ("relations", "children")
            for issue in issues.get(token, ())
        ):
            incomplete_children.add(ref)
    concrete_inputs = frozenset(
        reference[token]
        for token, node in snapshot.native.nodes.items()
        if token in reference and _concrete_input(node)
    )
    semantic_regions = frozenset(
        reference[token]
        for token, node in snapshot.native.nodes.items()
        if token in reference and _semantic_region(node)
    )
    contested_sources, splice_conflicts = _source_conflicts(snapshot, reference, children, issues)
    return CompressionEvidence(
        frozenset(complete),
        frozenset(wrappers),
        frozenset(descriptions),
        frozenset(unlabelled),
        children,
        frozenset(incomplete_children),
        capture_caption_sources(snapshot),
        concrete_inputs,
        semantic_regions,
        contested_sources,
        frozenset(reference[token] for token in reference if _incomplete_content(tuple(issues.get(token, ())))),
        splice_conflicts,
        _control_value_lines(snapshot, native_nodes, issues),
    )


def complete_destination(state: ProjectionView, node: ViewNode, field: str) -> bool:
    """Require every contributing source field to have a complete captured string."""
    evidence = state.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        return False
    parts = [part for part in node["textSources"] if part["field"] == field]
    return (
        all((part["sourceRef"], part["attribute"]) in evidence.complete_text for part in parts)
        if parts
        else (node["ref"], field) in evidence.complete_text
    )


def transparent_provider_wrapper(state: ProjectionView, node: ViewNode) -> bool:
    """Allow only the proven single-child provider boundary to be reconsidered."""
    evidence = state.context.get("compressionEvidence")
    return (
        isinstance(evidence, CompressionEvidence)
        and node["ref"] in evidence.provider_wrappers
        and len(node["children"]) == 1
        and not node.get("spatialPosition")
    )
