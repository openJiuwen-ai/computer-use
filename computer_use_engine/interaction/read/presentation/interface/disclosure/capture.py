# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prove projected excerpt fields are available in exact captured element details."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .model import CapturedTextField, DisclosurePolicy, DisclosureRequest
from .ranges import editing_ranges

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.ir import ProjectionInput
    from computer_use.interaction.read.presentation.records import NodeView, SnapshotFacts
    from computer_use.protocol.observation import NativeNode


_TEXT_FIELDS = ("name", "description", "value", "controlValue", "documentText")
_VALUE_FIELDS = frozenset(("value", "controlValue", "documentText"))
_EDITOR_ROLES = frozenset(("text_field", "text_area", "combo_box", "document"))
_ACTIVE_ROLES = frozenset(("button", "link", "textField", "textArea", "comboBox", "sheet"))
_LABEL_RELATIONS = frozenset(("labelled_by", "labels", "described_by", "describes", "error"))
_PROTECTED_RELATION_PROPERTIES = frozenset(("labelled_by", "label_for", "described_by", "description_for", "error"))


def _native_value(node: NativeNode, path: tuple[str, ...]) -> object:
    """Read an existing typed source path without traversing another element."""
    current: object = node
    for part in path:
        current = current.get(part) if isinstance(current, Mapping) else getattr(current, part, None)
    return current


def _active_text(node: NativeNode) -> bool:
    """Identify caret or selection evidence whose full context should stay inline."""
    text = node.text
    if text is None:
        return False
    return (
        text.caret_active is True
        or bool(text.selected_text)
        or any(
            span.collapsed is not True or span.start != span.end or bool(span.text) for span in text.selections or ()
        )
    )


def _covers_reported_text(node: NativeNode, path: tuple[str, ...], value: str) -> bool:
    """Compare a reported full-text count only in its declared offset unit."""
    text = node.text
    if text is None or text.character_count is None or path != ("text", "content") and path != text.content_field:
        return True
    if text.unit == "unicode_codepoint":
        return len(value) == text.character_count
    if text.unit == "utf16_code_unit":
        return len(value.encode("utf-16-le", errors="surrogatepass")) // 2 == text.character_count
    return False


def _protected_context(ref: str, source: ProjectionInput) -> tuple[str, ...]:
    """Retain focused, selected and actionable contexts through the original parent chain."""
    reasons: list[str] = []
    current: str | None = ref
    visited: set[str] = set()
    while current is not None:
        if current in visited:
            reasons.append("conflicting-parent-chain")
            break
        visited.add(current)
        node = source.native_nodes.get(current)
        if node is None:
            reasons.append("missing-parent-context")
            break
        if node.get("role") in _ACTIVE_ROLES or node.get("subrole") in ("dialog", "systemDialog"):
            reasons.append("interactive-or-dialog-context")
        if any(node.get(name) is True for name in ("selected", "editable", "modal", "busy", "invalid")):
            reasons.append("active-state")
        if node.get("selectedText") or node.get("error"):
            reasons.append("selection-or-error")
        selection = node.get("selectionRange")
        if isinstance(selection, Mapping) and isinstance(selection.get("length"), int) and selection["length"] > 0:
            reasons.append("text-selection")
        capabilities = node.get("capabilities", {})
        if any(capabilities.get(name) == "available" for name in ("typeText", "replaceText")):
            reasons.append("editable-context")
        if current == ref:
            if node.get("focused") is True:
                reasons.append("focused-field")
            if any(capabilities.get(name) == "available" for name in ("click", "typeText", "replaceText")):
                reasons.append("independent-input")
            if node.get("actions"):
                reasons.append("independent-actions")
        current = source.parents.get(current)
    return tuple(dict.fromkeys(reasons))


def capture_disclosure(
    snapshot: SnapshotFacts,
    document: InterfaceDocument,
    *,
    enabled: bool,
    policy: DisclosurePolicy | None = None,
) -> DisclosureRequest:
    """Find complete strings supported by direct element-detail routes.

    Args:
        snapshot: Frozen captured facts retained by the published observation.
        document: Structured interface with unchanged source fields and native bindings.
        enabled: True only for a public whole-view observation with read_details support.
        policy: Optional local display policy; the default favors substantial savings.

    Returns:
        Verified candidates. Missing, derived, ambiguous or partial fields remain inline.
    """
    return capture_projected_disclosure(
        snapshot, document.source, document.projected_nodes, enabled=enabled, policy=policy
    )


def capture_projected_disclosure(
    snapshot: SnapshotFacts,
    source: ProjectionInput,
    projected_nodes: Mapping[str, NodeView],
    *,
    enabled: bool,
    policy: DisclosurePolicy | None = None,
) -> DisclosureRequest:
    """Capture the same disclosure contract for a prepared projection before block wrapping."""
    selected_policy = policy or DisclosurePolicy()
    if not enabled:
        return DisclosureRequest((), False, selected_policy)
    # The public per-element detail resolver rejects ambiguous native aliases.
    tokens = tuple(snapshot.refs.values())
    if len(tokens) != len(set(tokens)):
        return DisclosureRequest((), True, selected_policy)
    native_records = {record["node"]: record for record in snapshot.facts["nodes"]}
    fields_by_ref: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    for (owner, native_field), normalized in snapshot.fields.items():
        fields_by_ref.setdefault(owner, []).append((native_field, normalized))
    label_tokens: set[str] = set()
    for relation in snapshot.facts["relations"]:
        # Property identifies the provider's relationship record; kind carries
        # its portable meaning. Keep explicit property-level protection as well
        # when an unknown relationship has not acquired a normalized label kind.
        if relation.get("kind") in _LABEL_RELATIONS or relation.get("property") in _PROTECTED_RELATION_PROPERTIES:
            label_tokens.add(relation["source"])
            label_tokens.update(relation.get("targets", ()))
    fields: list[CapturedTextField] = []
    for ref, projected in projected_nodes.items():
        token = snapshot.refs.get(ref)
        native = snapshot.native.nodes.get(token)
        record = native_records.get(token)
        if (
            native is None
            or record is None
            or record.get("observed") is False
            or not ref.isdecimal()
            or str(int(ref)) != ref
        ):
            continue
        passive = native.role == "static_text" or (
            native.role == "document" and native.text is not None and native.text.read_only is True
        )
        editor = native.role in _EDITOR_ROLES or native.states.editable is True
        if not (passive or editor) or native.states.protected is True:
            continue
        protections = list(_protected_context(ref, source))
        if _active_text(native) or native.states.selected is True or native.states.focused is True:
            protections.append("native-active-text")
        if record.get("actions", {}).get("state") != "complete":
            protections.append("incomplete-action-capabilities")
        if token in label_tokens:
            protections.append("label-or-error-relation")
        for attribute in _TEXT_FIELDS:
            editing_value = editor and attribute in _VALUE_FIELDS
            if not passive and not editing_value:
                continue
            value = projected.get(attribute)
            if not isinstance(value, str) or len(value) < selected_policy.minimum_characters:
                continue
            matches = []
            for native_field, normalized in fields_by_ref.get(ref, ()):
                if (
                    normalized.get("representation") != "field"
                    or normalized.get("field") != attribute
                    or normalized.get("node") != token
                    or type(normalized.get("normalized")) is not str
                    or normalized["normalized"] != value
                    or native_field not in record.get("requestedFields", ())
                    or type(record.get("values", {}).get(native_field)) is not str
                    or record["values"][native_field] != value
                ):
                    continue
                path = tuple(normalized.get("path", ()))
                status = record.get("fieldStatus", {}).get(native_field)
                if (
                    not path
                    or type(_native_value(native, path)) is not str
                    or _native_value(native, path) != value
                    or not _covers_reported_text(native, path, value)
                    or status is not None
                    and status.get("state") != "complete"
                ):
                    continue
                incomplete = any(
                    issue.path[:2] == ("nodes", token)
                    and (issue.path[2:][: len(path)] == path or path[: len(issue.path[2:])] == issue.path[2:])
                    for issue in snapshot.native.issues
                )
                if incomplete:
                    continue
                matches.append((native_field, path))
            if len(matches) == 1:
                native_field, path = matches[0]
                field_protections = list(protections)
                required_ranges = ()
                if editing_value:
                    # Input ownership, focus and actions stay on the same atom.
                    # Only the recoverable value payload may be shortened.
                    field_protections = []
                    if token in label_tokens:
                        field_protections.append("label-or-error-relation")
                    required = editing_ranges(native, path, value, projected)
                    range_paths = (
                        ("text", "selections"),
                        ("text", "caret"),
                        ("text", "caret_active"),
                        ("text", "selected_text"),
                        ("text", "unit"),
                        ("text", "source"),
                        ("text", "content_field"),
                        ("text", "character_count"),
                    )
                    uncertain_ranges = any(
                        issue.path[:2] == ("nodes", token)
                        and any(
                            issue.path[2:][: len(key)] == key or key[: len(issue.path[2:])] == issue.path[2:]
                            for key in range_paths
                        )
                        for issue in snapshot.native.issues
                    )
                    if required is None or uncertain_ranges:
                        field_protections.append("unresolved-editing-range")
                    else:
                        required_ranges = required
                fields.append(
                    CapturedTextField(
                        ref,
                        attribute,
                        native_field,
                        value,
                        token,
                        (snapshot.native.backend, snapshot.native.session, snapshot.native.id),
                        tuple(dict.fromkeys(field_protections)),
                        required_ranges,
                    )
                )
    return DisclosureRequest(tuple(fields), True, selected_policy)
