# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.text_context import (
    embedded_summaries,
    line_summaries,
    relation_summaries,
    supplements,
    unit_label,
)

"Cross-platform summaries of independent text and collection facts."


def read_summaries(facts: Mapping[str, Any], refs: Mapping[str, str], *, compact: bool = False) -> list[str]:
    "Present significant reading context from the normalized fact index."
    aliases = {token: ref for ref, token in refs.items()}
    output = relation_summaries(facts, aliases)
    output.extend(geometry_summaries(facts, aliases))
    passive_selections = {}
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
                (
                    f"Collection {ref}: {mode}; exposed children do not establish the logical total. Scroll "
                    f"or continue reading to explore more."
                )
            )
        if values.get("states.virtualized") is True:
            output.append(f"Item {ref}: virtualized; some properties may be unavailable in this observation.")
        if values.get("role") in ("document", "web_area") and not any(
            key in values for key in ("text.content", "text.content_field")
        ):
            state = node["fieldStatus"].get("text.content", {}).get("state", "not collected")
            output.append(f"Document text of {ref}: {state}; control values are recorded separately.")
        if "text.source" not in values:
            continue
        selections = values.get("text.selections")
        incidental_selection = (
            values.get("role") not in ("text_field", "text_area", "combo_box", "document", "web_area")
            and not values.get("states.focused")
            and not values.get("text.selected_text")
            and selections is not None
            and len(selections) == 1
            and selections[0].get("start") == 0
            and selections[0].get("end") == 0
            and selections[0].get("text") in (None, "")
            and "text.selections" not in node["fieldStatus"]
        )
        if selections is not None and not incidental_selection:
            pieces = []
            for index, span in enumerate(selections):
                label = "empty range" if span.get("collapsed") is True else "range"
                content = span.get("text")
                excerpt = (
                    "text unavailable"
                    if content is None
                    else literal(content[:160]) + ("…" if len(content) > 160 else "")
                )
                position = (
                    f"; offsets [{span['start']}, {span['end']}) {values.get('text.unit', 'unspecified unit')}"
                    if "start" in span and "end" in span
                    else ""
                )
                pieces.append(f"{label} {index + 1}: {excerpt}{position}")
            statuses = [status for name, status in node["fieldStatus"].items() if name.startswith("text.selections")]
            state = node["fieldStatus"].get("text.selections", {}).get("state", "partial" if statuses else "complete")
            coverage = supplements(node).get("text.selections.coverage", {})
            if coverage.get("conflicting"):
                state = "conflict"
            total = coverage.get("total")
            # Exact types preserve scalar validation and identity contracts.
            # pylint: disable-next=unidiomatic-typecheck
            count = f"{len(selections)}/{total}" if type(total) is int else str(len(selections))
            suffix = (
                f" ({state}; {count} ranges): "
                + ("; ".join(pieces) or "none")
                + ". Keyboard focus and caret activity are separate facts."
            )
            passive = (
                compact
                and state == "complete"
                and values.get("states.focused") is False
                and selections
                and all(span.get("collapsed") is True and span.get("text") == "" for span in selections)
            )
            if passive:
                passive_selections.setdefault(suffix, []).append(ref)
            else:
                output.append("Text selection of " + ref + suffix)
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
                f"Caret of {ref} (active={activity}): {position}; keyboard focus and selection are independent."
            )
        output.extend(embedded_summaries(node, ref, aliases))
        output.extend(line_summaries(node, ref))
        if not compact:
            for key, label in (("text.visible_ranges", "Visible text ranges"),):
                if key in values:
                    output.append(f"{label} of {ref}: " + literal(values[key]))
    for suffix, members in passive_selections.items():
        label = members[0] if len(members) == 1 else "elements " + ", ".join(members)
        output.append("Text selection of " + label + suffix)
    return output


def geometry_summaries(facts: Mapping[str, Any], aliases: Mapping[str, str]) -> list[str]:
    "Locate fully observed unnamed action leaves in their original coordinate space."
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
        matches_rectangle = (
            True
            if (
                not all(type(number) in (float, int) for number in rectangle)
                or rectangle[2] <= 0
                or rectangle[3] <= 0
                or bounds.get("valid") is False
            )
            else False
        )
        if matches_rectangle:
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
