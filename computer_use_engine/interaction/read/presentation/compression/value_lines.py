# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prove complete passive captions occur as exact lines of a native control value."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.ir import FactKey, SourceKey
from computer_use.interaction.read.presentation.tree import has_incomplete_children, VALUE_PREVIEW_LIMIT
from computer_use.protocol.primitives import utf16_length

if TYPE_CHECKING:
    from collections.abc import Sequence
    from computer_use.interaction.read.presentation.ir import ProjectionView, ViewNode
    from computer_use.protocol.records import Record


def _owned(view: ProjectionView, owner: str, ref: str, evidence: CompressionEvidence) -> bool:
    """Require a complete local source path from a caption to its own control."""
    current, seen = ref, set()
    while current not in seen:
        seen.add(current)
        source = view.native_nodes.get(current)
        if (
            source is None
            or view.scopes.get(current) != view.scopes.get(owner)
            or current in evidence.contested_sources
            or current in evidence.incomplete_children
            or current in evidence.incomplete_content
            or has_incomplete_children(source)
        ):
            return False
        if current == owner:
            return current != ref
        parent = view.parents.get(current)
        if parent is None or current not in evidence.complete_children.get(parent, ()):
            return False
        current = parent
    return False


def covered_value_lines(view: ProjectionView, owner: ViewNode, parts: Sequence[Record]) -> tuple[Record, ...] | None:
    """Locate exact caption occurrences in the owner's complete original Value.

    Match whole lines in original order, preserving all whitespace and line endings
    in the unchanged owner field. Ranges use UTF-16 code units of that field's string;
    they do not refer to a provider text cursor or a native selection coordinate.

    Args:
        view: Immutable source fields, ownership and exact field provenance.
        owner: Retained control whose native text explicitly aliases its control value.
        parts: Complete source-field strings to cover, in their original order.

    Returns:
        Exact source fields and nonoverlapping owner-string ranges, or None when any
        occurrence lacks complete source evidence or a whole matching line.
    """
    evidence = view.context.get("compressionEvidence")
    ref = owner["ref"]
    if (
        owner["role"] not in ("textField", "textArea", "comboBox")
        or not parts
        or not isinstance(evidence, CompressionEvidence)
        or ref not in evidence.control_value_lines
        or (ref, "value") not in evidence.complete_text
    ):
        return None
    value = view.field_value(ref, "value")
    original = view.native_nodes[ref].get("value")
    anchor = FactKey(SourceKey(ref), "value")
    if (
        type(original) is not str
        or not original
        or utf16_length(original) > VALUE_PREVIEW_LIMIT
        or ";base64," in original.lower()
        or value is None
        or len(value.parts) != 1
        or value.parts[0].facts != (anchor,)
        or value.value != original
    ):
        return None
    lines = tuple(re.finditer(r"[^\r\n\x85\u2028\u2029]+", original))
    result, offset, seen = [], 0, set()
    for part in parts:
        source, field, text = part["sourceRef"], part["attribute"], part["text"]
        if (
            (source, field) in seen
            or (source, field) not in evidence.complete_text
            or field not in ("name", "description", "value")
            or type(text) is not str
            or not text
            or view.native_nodes.get(source, {}).get(field) != text
            or not _owned(view, ref, source, evidence)
        ):
            return None
        presented = view.field_value(source, field)
        fact = FactKey(SourceKey(source), field)
        if (
            presented is None
            or len(presented.parts) != 1
            or presented.parts[0].facts != (fact,)
            or presented.value != text
        ):
            return None
        match = next((line for line in lines if line.start() >= offset and line.group() == text), None)
        if match is None:
            return None
        seen.add((source, field))
        offset = match.end()
        result.append(
            dict(
                sourceRef=source,
                attribute=field,
                targetRef=ref,
                field="value",
                start=utf16_length(original[: match.start()]),
                end=utf16_length(original[: match.end()]),
                unit="utf16_code_unit",
            )
        )
    return tuple(result)
