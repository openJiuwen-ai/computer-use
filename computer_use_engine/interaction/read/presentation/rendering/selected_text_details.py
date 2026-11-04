# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain redundant known-empty selection text in exact captured details."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.ir import FactKey, SourceKey
from computer_use.interaction.read.presentation.rendering.native_descriptions import NativeDescriptionField
from computer_use.interaction.read.presentation.selection_summary import selection_summary_plan

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import SnapshotFacts


_TEXT_FIELDS = ("text.source", "text.unit", "text.selections", "text.selected_text")
_TEXT_PATHS = tuple(tuple(field.split(".")) for field in _TEXT_FIELDS)


def _matches_source(
    mapping: Mapping[str, object] | None, token: str, path: tuple[str, ...], representation: str, field: str | None
) -> bool:
    """Require the original field's native owner, path and normalized representation."""
    return bool(
        mapping is not None
        and mapping.get("node") == token
        and tuple(mapping.get("path", ())) == path
        and mapping.get("representation") == representation
        and mapping.get("field") == field
    )


def capture_empty_selected_text_fields(
    snapshot: SnapshotFacts,
    projection: ProjectionResult,
) -> Mapping[FactKey, NativeDescriptionField]:
    """Select empty aggregate text already explained by a complete collapsed range.

    Args:
        snapshot: Original native selection facts, field mappings and source bindings.
        projection: Immutable source hierarchy and current display candidates.

    Returns:
        Same-capture detail destinations for exact empty selectedText fields. Each
        entry requires one complete collapsed range, matching native coordinates
        and units, and the existing public selection summary. It leaves focused,
        caret, value and per-range text facts unchanged.
    """
    original = {node["node"]: node for node in snapshot.facts["nodes"]}
    counts = Counter(snapshot.refs.values())
    blocked: set[str] = set()
    for issue in snapshot.native.issues:
        if not issue.path or issue.path == ("nodes",) and issue.state != "partial":
            return MappingProxyType({})
        if len(issue.path) < 2 or issue.path[0] != "nodes":
            continue
        path = issue.path[2:]
        if not path or any(path[: len(target)] == target or target[: len(path)] == path for target in _TEXT_PATHS):
            blocked.add(str(issue.path[1]))
    summaries = selection_summary_plan(snapshot.facts, snapshot.refs, roots=snapshot.roots)
    result: dict[FactKey, NativeDescriptionField] = {}
    for ref, scope in projection.source.scopes.items():
        token = snapshot.refs.get(ref)
        native = snapshot.native.nodes.get(token)
        fact = original.get(token)
        node = projection["nativeNodes"].get(ref)
        source_node = projection.source.native_nodes.get(ref)
        summary = summaries.entries.get(ref)
        if (
            token is None
            or token in blocked
            or counts[token] != 1
            or native is None
            or native.text is None
            or fact is None
            or node is None
            or source_node is None
            or summary is None
            or type(native.text.selected_text) is not str
            or native.text.selected_text != ""
            or type(node.get("selectedText")) is not str
            or node["selectedText"] != ""
            or type(source_node.get("selectedText")) is not str
            or source_node["selectedText"] != ""
        ):
            continue
        values = fact["values"]
        if (
            type(values.get("text.selected_text")) is not str
            or values["text.selected_text"] != ""
            or values.get("text.source") != native.text.source
            or values.get("text.unit") != native.text.unit
            or any(
                key == field or key.startswith(field + ".") or field.startswith(key + ".")
                for key in fact["fieldStatus"]
                for field in _TEXT_FIELDS
            )
        ):
            continue
        selected = snapshot.fields.get((ref, "text.selected_text"))
        if (
            not _matches_source(selected, token, ("text", "selected_text"), "field", "selectedText")
            or type(selected.get("normalized")) is not str
            or selected["normalized"] != ""
            or not _matches_source(snapshot.fields.get((ref, "text.source")), token, ("text", "source"), "raw", None)
            or not _matches_source(snapshot.fields.get((ref, "text.unit")), token, ("text", "unit"), "raw", None)
            or not _matches_source(
                snapshot.fields.get((ref, "text.selections")), token, ("text", "selections"), "annotation", None
            )
        ):
            continue
        spans = native.text.selections
        if (
            native.text.unit not in ("utf16_code_unit", "unicode_codepoint")
            or spans is None
            or len(spans) != 1
            or spans[0].collapsed is not True
            or type(spans[0].start) is not int
            or type(spans[0].end) is not int
            or spans[0].start < 0
            or spans[0].start != spans[0].end
            or spans[0].text not in (None, "")
            or summary.source != token
            or summary.read_state != "complete"
            or summary.unit != native.text.unit
            or summary.interval != (spans[0].start, 0)
            or len(summary.spans) != 1
            or any(
                summary.spans[0].get(field) != getattr(spans[0], field)
                for field in ("start", "end", "collapsed", "text")
            )
        ):
            continue
        if not all(
            isinstance(candidate.get("selectionRange"), Mapping) and summaries.covers(ref, candidate["selectionRange"])
            for candidate in (node, source_node)
        ):
            continue
        key = FactKey(SourceKey(ref), "selectedText")
        result[key] = NativeDescriptionField(
            ref,
            token,
            (snapshot.native.session, snapshot.native.id, scope),
            "text.selected_text",
            "selectedText",
            ("text", "selected_text"),
            "",
        )
    return MappingProxyType(result)
