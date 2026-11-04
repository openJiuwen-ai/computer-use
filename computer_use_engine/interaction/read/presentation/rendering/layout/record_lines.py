# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed source-line evidence shared by reversible record-table layouts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import re

from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.output import RenderedNode
from computer_use.interaction.read.presentation.tree import has_incomplete_children
from computer_use.protocol.primitives import utf16_length

_NUMBER = r"(?:0|[1-9][0-9]*)"
_NODE = re.compile(r"^(\t*)(" + _NUMBER + r") (.+)\Z")
_SCOPES = frozenset(("application", "window", "sheet", "menuBar", "menu"))


@dataclass(frozen=True)
class GeneratedLine:
    """One still-unpacked generated element line in the current layout."""

    node: NodeView
    record: RenderedNode
    index: int
    indent: str
    prefix: str
    body: str


def safe_record_text(text: str) -> bool:
    return not any(value in text for value in ("|", ";", "\r", "\n", "\t", "\\r", "\\n", "\\t"))


def generated_lines(
    frame: LayoutFrame, *, allow_reindent: bool = False
) -> tuple[dict[str, GeneratedLine], dict[str, tuple[str, int]]]:
    """Validate generated identities, single-line spans and complete local parents.

    Args:
        frame: Current lines and unchanged structured source records.
        allow_reindent: Accept each original record at its recorded depth. The
            caller must then prove one uniform offset across its entire record.
    """
    nodes: dict[str, NodeView] = {}
    occurrences: Counter[str] = Counter()
    positions: dict[str, tuple[str, int]] = {}
    pending = list(frame.roots)
    while pending:
        parent = pending.pop()
        ref = parent["ref"]
        nodes[ref] = parent
        occurrences[ref] += 1
        pending.extend(parent["children"])
        if not has_incomplete_children(parent):
            for position, child in enumerate(parent["children"]):
                positions[child["ref"]] = (ref, position)
    records = {record.ref: record for record in frame.rendered_nodes}
    counts = Counter(record.ref for record in frame.rendered_nodes)
    line_counts = Counter(match[2] for line in frame.lines if (match := _NODE.fullmatch(line)))
    result: dict[str, GeneratedLine] = {}
    for index, line in enumerate(frame.lines):
        match = _NODE.fullmatch(line)
        if match is None:
            continue
        ref = match[2]
        node, record = nodes.get(ref), records.get(ref)
        prefix = frame.node_prefixes.get(ref)
        span = frame.node_line_ranges.get(ref)
        if (
            node is None
            or record is None
            or prefix is None
            or span is None
            or occurrences[ref] != 1
            or counts[ref] != 1
            or line_counts[ref] != 1
            or span[1] - span[0] != 1
            or "\n" in record.text
            or record.partial
            or record.hints
            or record.related
            or has_incomplete_children(node)
            or node["role"] in _SCOPES
            or node.get("subrole") in ("dialog", "systemDialog", "popover")
            or not record.text.startswith(("\t" * record.depth if allow_reindent else match[1]) + ref + " " + prefix)
            or not (match[3] == prefix or match[3].startswith(prefix + " ") or match[3].startswith(prefix + ", "))
            or not safe_record_text(match[3])
            or utf16_length(match[3]) > 320
        ):
            continue
        result[ref] = GeneratedLine(node, record, index, match[1], prefix, match[3])
    return result, positions


def point_position(node: NodeView, key: str) -> int | None:
    value = node.get(key)
    if isinstance(value, Mapping) and type(value.get("length")) is int and value["length"] == 1:
        location = value.get("location")
        if type(location) is int and location >= 0:
            return location + 1
    return None
