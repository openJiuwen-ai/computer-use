# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cross-platform summaries of independent text and collection facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.selection_summary import SelectionSummaryPlan, selection_summary_plan
from computer_use.interaction.read.presentation.text_context import (
    embedded_summaries,
    line_summaries,
    relation_summaries,
    unit_label,
)


def read_summaries(
    facts: Mapping[str, Any],
    refs: Mapping[str, str],
    *,
    compact: bool = False,
    roots: Sequence[Mapping[str, Any]] = (),
    selection_plan: SelectionSummaryPlan | None = None,
    omit_unspecified_document_text: bool = False,
    defer_inactive_selections: bool = False,
    concise: bool = False,
) -> list[str]:
    """Present significant reading context from the normalized fact index."""
    aliases = {token: ref for ref, token in refs.items()}
    output = relation_summaries(facts, aliases)
    if not compact:
        output.extend(geometry_summaries(facts, aliases))
    selections_plan = selection_plan or selection_summary_plan(facts, refs, roots=roots)
    for relation in facts["relations"]:
        if relation.get("enumeration") == "sampled" and relation["state"] not in ("absent", "unsupported"):
            ref = aliases[relation["source"]]
            if relation["kind"] == "viewport_candidates":
                output.append(
                    f"Viewport candidates of {ref}: sampled; full viewport coverage and occlusion are unknown."
                )
            else:
                output.append(
                    f"Relation {relation['kind']} of {ref}: sampled targets; complete collection membership is unknown."
                )
    for node in facts["nodes"]:
        ref, values = aliases[node["node"]], node["values"]
        mode = values.get("structure.collection_mode")
        if mode in ("virtualized", "provider_managed"):
            output.append(
                f"Collection {ref}: {mode}; exposed children do not establish the logical total. Scroll or continue reading to explore more."
            )
        if values.get("states.virtualized") is True:
            output.append(f"Item {ref}: virtualized; some properties may be unavailable in this observation.")
        if values.get("role") in ("document", "web_area") and not any(
            key in values for key in ("text.content", "text.content_field")
        ):
            if not (compact and omit_unspecified_document_text and _unspecified_document_text(node, facts)):
                state = node["fieldStatus"].get("text.content", {}).get("state", "not collected")
                output.append(
                    f"Document text [{ref}]: {state}; control values separate."
                    if compact
                    else f"Document text of {ref}: {state}; control values are recorded separately."
                )
        if "text.source" not in values:
            continue
        selection = selections_plan.entries.get(ref)
        if (
            selection is not None
            and (not compact or selection.group is None)
            and not (compact and defer_inactive_selections and selection.inactive)
        ):
            output.append(selection.statement(ref, concise=concise) if compact else selection.detail_statement())
        caret = {
            key: values[source]
            for key, source in (("active", "text.caret_active"), ("range", "text.caret"))
            if source in values
        }
        if caret:
            span = caret.get("range", {})
            position = (
                f"{unit_label(values.get('text.unit'))} {span['start']}..{span['end']}"
                if "start" in span and "end" in span
                else "position unconfirmed"
            )
            activity = literal(caret["active"]) if "active" in caret else "unknown"
            output.append(
                f"Caret [{ref}]: active={activity}; {position}"
                if compact
                else f"Caret of {ref} (active={activity}): {position}; keyboard focus and selection are independent."
            )
        output.extend(embedded_summaries(node, ref, aliases))
        output.extend(line_summaries(node, ref))
        if not compact:
            for key, label in (("text.visible_ranges", "Visible text ranges"),):
                if key in values:
                    output.append(f"{label} of {ref}: " + literal(values[key]))
    if compact:
        output.extend(selections_plan.grouped_lines(concise=concise, defer_inactive=defer_inactive_selections))
    return output


def _unspecified_document_text(node: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    """Recognize an absent body field without a reported body-reading restriction.

    This only classifies the renderer's synthesized explanation. Original body,
    control values and read diagnostics retain their existing delivery paths.
    """

    def body_path(path: tuple[str | int, ...]) -> bool:
        return (
            not path
            or path[0] == "role"
            or path[0] == "text"
            and (len(path) == 1 or path[1] in ("content", "content_field"))
        )

    for name in node["fieldStatus"]:
        path = tuple(node["fieldPaths"].get(name, ()))
        if body_path(path):
            return False
    for issue in facts.get("issues", ()):
        path = tuple(issue["path"])
        if len(path) < 2 or path[0] != "nodes" or path[1] == node["node"] and body_path(path[2:]):
            return False
    return True


def geometry_summaries(facts: Mapping[str, Any], aliases: Mapping[str, str]) -> list[str]:
    """Locate fully observed unnamed action leaves in their original coordinate space."""
    groups = {}
    relations = {}
    for edge in facts["relations"]:
        if edge["kind"] == "children":
            relations.setdefault(edge["source"], []).append(edge)
    for node in facts["nodes"]:
        values = node["values"]
        if values.get("name") != "" or values.get("description") or values.get("control_value.value"):
            continue
        if (
            values.get("states.visible") is False
            or values.get("states.showing") is False
            or not (values.get("states.showing") is True or values.get("states.offscreen") is False)
        ):
            continue
        if not node["actions"].get("items") or node["actions"]["state"] != "complete":
            continue
        children = relations.get(node["node"], [])
        if len(children) != 1 or children[0]["state"] != "complete" or children[0]["targets"]:
            continue
        if any(key.startswith("geometry.bounds") or key == "name" for key in node["fieldStatus"]):
            continue
        bounds = values.get("geometry.bounds", {})
        rectangle = [bounds.get(name) for name in ("x", "y", "width", "height")]
        if (
            not all(type(number) in (float, int) for number in rectangle)
            or rectangle[2] <= 0
            or rectangle[3] <= 0
            or bounds.get("valid") is False
        ):
            continue
        space = bounds.get("space")
        metadata = facts.get("coordinateSpaces", {}).get(space)
        if metadata is None:
            continue
        groups.setdefault(space, []).append(
            aliases[node["node"]] + "=(" + ", ".join(format(number, "g") for number in rectangle) + ")"
        )
    output = []
    for space, entries in groups.items():
        metadata = facts["coordinateSpaces"][space]
        unit = {"physical_pixel": "physical screen pixels", "logical_pixel": "logical pixels", "point": "points"}.get(
            metadata["unit"], "provider units"
        )
        output.append(
            "Unlabeled element bounds ("
            + unit
            + "; "
            + metadata["origin"]
            + "): "
            + "; ".join(entries)
            + ". These are not App PNG coordinates and do not prove a point is clickable."
        )
    return output
