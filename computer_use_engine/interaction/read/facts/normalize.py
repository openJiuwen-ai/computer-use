# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build presentation views from platform-normalized accessibility facts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from computer_use.interaction.read.facts.groups import render_properties
from computer_use.interaction.read.facts.index import data, field_name, observation_index
from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.records import ReferenceAllocator
from computer_use.protocol.observation import NativeNode, NativeObservation
from computer_use.protocol.primitives import required_item, safe_integer

_ROLES = {
    "checkbox": "checkBox",
    "popup_button": "popUpButton",
    "scrollbar": "scrollBar",
    "static_text": "staticText",
    "tree": "outline",
    "spinner": "incrementor",
    "list_item": "row",
    "tree_item": "row",
    "dialog": "window",
    "alert": "window",
}
_FIELDS = {
    "role_description": "roleDescription",
    "text.selected_text": "selectedText",
    "states.checked": "toggleState",
    "structure.heading_level": "headingLevel",
    "structure.landmark": "landmarkType",
    "structure.landmark_description": "localizedLandmarkType",
    "structure.level": "disclosureLevel",
    "structure.row_count": "rowCount",
    "structure.column_count": "columnCount",
    "structure.index": "index",
    "structure.selected_count": "selectedChildCount",
}
_LABELS = {"name", "description", "help", "identifier", "placeholder", "url"}
_DISPLAY_STATES = {"enabled", "focused", "focusable", "selected", "selectable", "modal", "busy", "visible", "showing"}
_VALUE_FIELDS = {
    "control_value.value": "controlValue",
    "text.content": "documentText",
    "numeric_value.value": "rangeValue",
}
_STRUCTURAL_FIELDS = {"text.source", "text.unit", "text.content_field"}
_ISSUES_PRESERVING_CONTENT = {"partial", "error", "timeout", "unavailable", "redacted", "stale", "conflict"}


def presentation_role(role: str | None) -> str:
    """Adapt public role spellings to the existing presentation IR vocabulary."""
    if role is None:
        return "unknown"
    return _ROLES.get(role, role.split("_")[0] + "".join(part.title() for part in role.split("_")[1:]))


def _value_text(value: Any, aliases: dict[str, str]) -> str:
    """Format a public scalar while preserving element-valued identity."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value) == {"element"}:
        return "element " + aliases[value["element"]]
    if isinstance(value, dict) and set(value) in ({"datetime"}, {"number"}):
        return next(iter(value.values()))
    return literal(value)


def _primary_value(source: dict[str, Any], node: NativeNode) -> str | None:
    """Choose a display value; independent value channels remain separately indexed."""
    values = source["values"]
    text_source = field_name(node.text.content_field) if node.text and node.text.content_field else "text.content"
    if node.role in ("document", "web_area") and (node.text is not None or "text.content" in source["fieldStatus"]):
        return text_source if text_source in values else None
    return next((key for key in ("control_value.value", text_source, "numeric_value.value") if key in values), None)


def normalize_facts(
    value: NativeObservation,
    references: ReferenceAllocator,
    *,
    driver_features: Iterable[str] = (),
    scope_kind: str | None = None,
    operation_capabilities: Mapping[str, Mapping[str, str]] | None = None,
) -> SemanticSnapshot:
    """Organize a unified fact graph into source-accounted presentation roots.

    Preserve independent values, relationship views, collection gaps and native
    identities. Platform interpretation is completed before this entry point.
    """
    if scope_kind not in (None, "window", "applicationMenu"):
        raise ValueError("Unknown observation scope kind")
    if not isinstance(value, NativeObservation):
        raise TypeError("normalizeFacts requires a normalized NativeObservation")
    if references.session != value.session:
        raise ValueError("Native display references belong to another session")
    batch = observation_index(value)
    ordered_tokens = [source["node"] for source in batch["nodes"] if source["observed"]]
    observed_tokens = set(ordered_tokens)
    ordered_tokens.extend(token for token in value.nodes if token not in observed_tokens)
    refs = {references.reference(token): token for token in ordered_tokens}
    aliases = {token: ref for ref, token in refs.items()}
    nodes, sources, text_refs, value_fields, grouped = {}, {}, {}, {}, {}
    annotations = []

    for source in batch["nodes"]:
        token, raw = source["node"], source["values"]
        original, ref = value.nodes[token], aliases[token]
        if not source["observed"] and token not in value.roots:
            for key, status in source["fieldStatus"].items():
                label = status["state"] + (": " + status["detail"] if status.get("detail") else "")
                grouped.setdefault((key, label), []).append(ref)
            continue
        node = dict(
            ref=ref,
            role=presentation_role(original.role),
            children=[],
            capabilities=dict(
                (operation_capabilities or {}).get(
                    token, {key: "unknown" for key in ("click", "typeText", "replaceText", "focus")}
                )
            ),
        )
        if original.role in ("dialog", "alert"):
            node["subrole"] = original.role
        elif original.role in ("list_item", "tree_item"):
            node["subrole"] = original.role
        nodes[token] = node
        primary = _primary_value(source, original)
        if primary is not None:
            value_fields[ref] = primary
        for key, item in raw.items():
            field = None
            if key == "role":
                field = "role"
            elif key in _LABELS:
                field = key
            elif key == "role_detail" and original.role not in ("dialog", "alert", "list_item", "tree_item"):
                field = "subrole"
            elif key in _FIELDS:
                candidate = _FIELDS[key]
                if candidate not in (
                    "headingLevel",
                    "disclosureLevel",
                    "rowCount",
                    "columnCount",
                    "index",
                    "selectedChildCount",
                ) or safe_integer(item):
                    field = candidate
            elif key.startswith("states.") and key[7:] in _DISPLAY_STATES:
                field = key[7:]
            elif key == "states.expanded" and item in ("expanded", "collapsed"):
                field, item = "expanded", item == "expanded"
            elif key in _VALUE_FIELDS:
                field = "value" if key == primary else _VALUE_FIELDS[key]
                item = _value_text(item, aliases)
                if field == "value":
                    if type(raw[key]) in (int, float):
                        node["valueIsNumber"] = True
                    if key == "text.content" and original.role in ("document", "web_area"):
                        node["valueKind"] = "document"
                if isinstance(raw[key], str) and key not in source["fieldStatus"]:
                    node.setdefault("_completeTextValues", []).append(field)
            if field is not None:
                if key != "role":
                    node[field] = item
                sources[(ref, key)] = dict(
                    node=token,
                    path=source["fieldPaths"][key],
                    field=field,
                    representation="field",
                    normalized=node[field],
                )
            else:
                structural = key in _STRUCTURAL_FIELDS
                sources[(ref, key)] = dict(
                    node=token,
                    path=source["fieldPaths"][key],
                    field=None,
                    representation="raw" if structural else "annotation",
                )
                if not structural:
                    grouped.setdefault((key, literal(item)), []).append(ref)
                provider_fact = key.startswith("supplements[") and item["key"].startswith("provider.")
                meaningful_extra = (
                    key.startswith("supplements[")
                    and item["key"] in ("item_status", "item_type")
                    and bool(item["value"])
                )
                invalid_geometry = key.startswith("geometry.") and isinstance(item, dict) and item.get("valid") is False
                matches_provider_fact = (
                    True
                    if (
                        provider_fact
                        or meaningful_extra
                        or invalid_geometry
                        or key in ("states.editable", "text.read_only")
                        and item is True
                    )
                    else False
                )
                if matches_provider_fact:
                    node["preserveGroup"] = True
        for axis in ("row", "column"):
            index, span = raw.get("structure." + axis + "_index"), raw.get("structure." + axis + "_span")
            keys = ("structure." + axis + "_index", "structure." + axis + "_span")
            matches_safe_integer = (
                True
                if (
                    safe_integer(index)
                    and safe_integer(span)
                    and index >= 0
                    and span >= 0
                    and safe_integer(index + span)
                    and not any(key in source["fieldStatus"] for key in keys)
                )
                else False
            )
            if matches_safe_integer:
                field = axis + "Range"
                node[field] = dict(location=index, length=span)
                for key in ("structure." + axis + "_index", "structure." + axis + "_span"):
                    sources[(ref, key)] = dict(
                        node=token,
                        path=source["fieldPaths"][key],
                        field=None,
                        representation="projection",
                        projections=[dict(field=field, normalized=node[field])],
                    )
                    annotation = (key, literal(raw[key]))
                    required_item(grouped, annotation).remove(ref)
                    if not required_item(grouped, annotation):
                        del grouped[annotation]
        matches_original = (
            True
            if (
                original.text
                and original.text.unit in ("unicode_codepoint", "utf16_code_unit")
                and original.text.selections
                and len(original.text.selections) == 1
            )
            else False
        )
        if matches_original:
            selection = original.text.selections[0]
            if selection.start is not None and selection.end is not None:
                node["selectionRange"] = dict(location=selection.start, length=selection.end - selection.start)
        if original.shortcuts:
            node["shortcut"] = "; ".join(shortcut.text for shortcut in original.shortcuts)
        node["actions"] = [
            dict(
                name=action.key,
                kind=action.kind,
                **({"description": action.description or action.label} if action.label or action.description else {}),
            )
            for action in original.actions or ()
            if action.key is not None
        ]
        if source["actions"]["state"] != "complete":
            node["actionsUnavailable"] = True
        for key, status in source["fieldStatus"].items():
            label = status["state"] + (": " + status["detail"] if status.get("detail") else "")
            grouped.setdefault((key, label), []).append(ref)
            if "textRef" in status:
                text_refs[(ref, key)] = status.copy()
                caption = "; captured text available"
                caption += (
                    " via this element's read_text()"
                    if value_fields.get(ref) == key
                    else "; no public continuation for this field"
                )
                if status.get("totalUnits") is not None:
                    unit = {"utf16_code_unit": "UTF-16 units", "unicode_codepoint": "Unicode code points"}.get(
                        status.get("unit"), status.get("unit") or "units"
                    )
                    caption += f"; {status['totalUnits']} {unit}"
                required_item(grouped, (key, label)).remove(ref)
                if not required_item(grouped, (key, label)):
                    del grouped[(key, label)]
                grouped.setdefault((key, label + caption), []).append(ref)
            incidental_range = (
                key.startswith("text.selections")
                and original.role not in ("text_field", "text_area", "combo_box", "document", "web_area")
                and not original.states.focused
                and not (original.text and original.text.selected_text)
            )
            if status["state"] in _ISSUES_PRESERVING_CONTENT and not incidental_range:
                node["preserveGroup"] = True

    children = {token: [] for token in nodes}
    child_views = {}
    for edge in batch["relations"]:
        if (
            edge["kind"] == "children"
            and edge["certainty"] == "confirmed"
            and edge["state"] not in ("absent", "unsupported")
        ):
            child_views.setdefault(edge["source"], []).append(edge)
    extra_edges = []
    for relation in batch["relations"]:
        token, kind, targets = relation["source"], relation["kind"], relation["targets"]
        source = nodes[token]
        complete = relation["state"] == "complete"
        confirmed = relation["certainty"] == "confirmed"
        if relation["state"] in ("absent", "unsupported"):
            extra_edges.append(relation)
            continue
        if kind == "children" and confirmed:
            if len(required_item(child_views, token)) > 1:
                source["preserveGroup"] = True
                source["partialReason"] = "multiple observed child views; inspect relationship details"
                extra_edges.append(relation)
                continue
            children[token].extend(targets)
            if not complete:
                if "total" in relation:
                    source["childCount"] = relation["total"]
                if "offset" in relation:
                    source.update(childrenStart=relation["offset"], childrenEnd=relation["offset"] + len(targets))
                    if "cursor" in relation:
                        source["nextChildOffset"] = source["childrenEnd"]
                source["partialReason"] = "observed children partly collected; see Relation details"
            if any(target in batch["unread"] for target in targets):
                source["partialReason"] = "child facts have not been collected"
        else:
            matches_confirmed_condition = (
                True
                if (
                    kind == "labelled_by"
                    and confirmed
                    and complete
                    and len(targets) == 1
                    and targets[0] not in batch["unread"]
                )
                else False
            )
            if matches_confirmed_condition:
                source["titleRef"] = aliases[targets[0]]
                extra_edges.append(relation)
            elif kind == "labels" and confirmed:
                source["labelTargets"] = [aliases[target] for target in targets]
                source["incompleteLabelRelation"] = not complete or any(target in batch["unread"] for target in targets)
                extra_edges.append(relation)
            else:
                matches_confirmed = (
                    True if kind == "disclosed_by" and confirmed and complete and len(targets) == 1 else False
                )
                if matches_confirmed:
                    source["disclosedBy"] = aliases[targets[0]]
                    extra_edges.append(relation)
                else:
                    extra_edges.append(relation)
        if not complete or not confirmed:
            source["preserveGroup"] = True

    placed, active = set(), set()

    def visit(token: str) -> dict[str, Any]:
        placed.add(token)
        active.add(token)
        pending = [(token, iter(children[token]))]
        while pending:
            parent, edges = pending[-1]
            child = next(edges, None)
            if child is None:
                active.remove(parent)
                pending.pop()
                continue
            node = nodes[parent]
            if child not in nodes:
                annotations.append(f"{node['ref']} contains {aliases[child]} (unread)")
                continue
            if child in active or child in placed:
                reason = "cycle" if child in active else "shared"
                annotations.append(f"{node['ref']} contains {aliases[child]} ({reason})")
                node["preserveGroup"] = True
            else:
                node["children"].append(nodes[child])
                placed.add(child)
                active.add(child)
                pending.append((child, iter(children[child])))
        return nodes[token]

    related_contexts, root_contexts, roots = {}, {}, []
    for relation in extra_edges:
        if relation["certainty"] == "confirmed" and relation["kind"] not in ("parent", "window"):
            for target in relation["targets"]:
                if target != relation["source"]:
                    item = (aliases[relation["source"]], relation["kind"])
                    if item not in related_contexts.setdefault(target, []):
                        related_contexts[target].append(item)
    for token in dict.fromkeys((*value.roots, *nodes)):
        if token in placed:
            continue
        if token not in value.roots:
            if token in related_contexts:
                root_contexts[aliases[token]] = tuple(related_contexts[token])
            elif token not in batch["unread"]:
                annotations.append(f"{aliases[token]} has no expanded containment path from the requested roots")
        roots.append(visit(token))
    annotations.extend(render_properties(grouped))
    if batch["unread"]:
        annotations.append("Uncollected: " + ", ".join(aliases[token] for token in batch["unread"]))
    annotations.extend("Collection: " + literal(reason) for reason in batch["stopReasons"])
    if value.coordinate_spaces:
        annotations.append("Coordinate spaces: " + literal(data(value.coordinate_spaces)))
    return SemanticSnapshot(
        value,
        batch,
        roots,
        refs,
        annotations,
        sources,
        text_refs,
        scope_kind,
        root_contexts,
        value_fields,
        frozenset(driver_features),
    )
