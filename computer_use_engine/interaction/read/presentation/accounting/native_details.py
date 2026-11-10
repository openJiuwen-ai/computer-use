# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate exact original-field supplements before declaring detail-only delivery."""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.fallback import literal

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.model import RenderedPart
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.records import Record


NATIVE_DIAGNOSTIC_FIELDS = MappingProxyType(
    {
        "valueDescription": ("control_value.description", ("control_value", "description")),
        "rangeDescription": ("numeric_value.description", ("numeric_value", "description")),
        "role": ("role", ("role",)),
        "roleDescription": ("role_description", ("role_description",)),
    }
)
_MAPPED_FIELDS = {
    **{native_property: (field, path) for field, (native_property, path) in NATIVE_DIAGNOSTIC_FIELDS.items()},
    "description": ("description", ("description",)),
    "text.selected_text": ("selectedText", ("text", "selected_text")),
}


def collect_native_details(
    snapshot: SnapshotFacts,
    parts: Sequence[RenderedPart],
    native_property: Literal[
        "identifier",
        "url",
        "description",
        "text.selected_text",
        "control_value.description",
        "numeric_value.description",
        "role",
        "role_description",
    ],
    *,
    references: frozenset[str] | None = None,
) -> tuple[tuple[Record, ...], ...]:
    """Locate existing ledger entries backed by real same-reference detail groups.

    Args:
        snapshot: Captured source fields and unchanged native reference bindings.
        parts: Already-accounted root presentations and their retained supplements.
        native_property: Independent native scalar whose delivery is being classified.
        references: Restrict classification to fields actually deferred by the renderer.

    Returns:
        The original accounting entries in root-part order.

    Raises:
        ValueError: An advertised detail field lacks its exact source or supplement.
    """
    native = {node["node"]: node for node in snapshot.facts["nodes"]}
    coverage = []
    title = {"identifier": "Identifier", "url": "URL"}.get(native_property, "Native " + native_property)
    mapped = _MAPPED_FIELDS.get(native_property)
    found: set[str] = set()
    for part in parts:
        records = []
        entries = () if part.uncompressed else part.rendered.get("factAccounting", {}).get("values", ())
        for entry in entries:
            ref = entry["ref"]
            if entry["property"] != native_property or entry["kind"] != "supplement":
                continue
            if references is not None and ref not in references:
                continue
            source = snapshot.fields.get((ref, native_property))
            token = snapshot.refs.get(ref)
            original = native.get(token, {}).get("values", {})
            value = original.get(native_property)
            normalized = (
                part.rendered["projection"].source.native_nodes.get(ref, {}).get("role")
                if native_property == "role"
                else value
            )
            expected_field, expected_path = mapped if mapped else (native_property, (native_property,))
            actual = getattr(snapshot.native.nodes.get(token), expected_path[0], None)
            if len(expected_path) == 2:
                actual = getattr(actual, expected_path[1], None)
            source_disagrees = (
                source is None
                or bool(source.get("field") != expected_field)
                or native_property not in original
            )
            source_disagrees = (
                source_disagrees
                or type(source.get("normalized")) is not type(normalized)
                or bool(source["normalized"] != normalized)
            )
            source_disagrees = (
                source_disagrees
                or (mapped is not None and (type(actual) is not type(value) or bool(actual != value)))
            )
            if not source_disagrees:
                source_disagrees = (bool(native_property == "url") or mapped is not None)
                source_disagrees = (
                    source_disagrees
                    and (bool(source.get("node") != token) or bool(tuple(source.get("path", ())) != expected_path))
                )
            if source_disagrees:
                raise ValueError(title + " supplement has no matching source field")
            label = native_property + ": " + literal(value)
            if ref not in part.supplements.get(label, ()):
                raise ValueError(title + " destination has no captured detail supplement")
            records.append(entry)
            found.add(ref)
        coverage.append(tuple(records))
    if references is not None and found != references:
        raise ValueError(title + " destination has no captured detail supplement")
    return tuple(coverage)


def collect_deferred_fields(
    snapshot: SnapshotFacts,
    parts: Sequence[RenderedPart],
) -> tuple[tuple[Record, ...], ...]:
    """Validate actual deferred diagnostics against their original native supplements.

    The renderer records only fields it omitted. Verify their capture, scalar and
    normalized/native path, then return the existing supplementary accounting entries.
    """
    native = {node["node"]: node for node in snapshot.facts["nodes"]}
    references: dict[str, set[str]] = {}
    for part in parts:
        for item in part.rendered.get("deferredNativeFields", ()):
            ref, native_property = item["ref"], item["property"]
            mapping = _MAPPED_FIELDS.get(native_property)
            source = snapshot.fields.get((ref, native_property))
            token = snapshot.refs.get(ref)
            original = native.get(token, {}).get("values", {})
            scopes = part.rendered["projection"].source.scopes
            if mapping is None or source is None or native_property not in original:
                raise ValueError("Deferred diagnostic has no matching captured native field")
            if item["native"] != token or item["field"] != mapping[0] or tuple(item["path"]) != mapping[1]:
                raise ValueError("Deferred diagnostic has no matching captured native field")
            if (
                tuple(item["scope"]) != (snapshot.native.session, snapshot.native.id, scopes.get(ref))
                or type(item["value"]) is not type(original[native_property])
                or item["value"] != original[native_property]
            ):
                raise ValueError("Deferred diagnostic has no matching captured native field")
            if item["readState"] != "complete" or item["status"] is not None:
                raise ValueError("Deferred diagnostic has no matching captured native field")
            references.setdefault(native_property, set()).add(ref)
    return tuple(
        records
        for native_property, refs in references.items()
        for records in collect_native_details(snapshot, parts, native_property, references=frozenset(refs))
    )
