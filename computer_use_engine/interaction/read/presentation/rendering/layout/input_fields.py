# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share exact unknown Click/Input fields within their original table."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.public_entries import (
    NumberedEntry,
    unique_numbered_entries,
    unquoted_positions,
)
from computer_use.interaction.read.presentation.rendering.layout.role_labels import expand_role_labels
from computer_use.interaction.read.presentation.tree import has_incomplete_children

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame

_FIELDS = "Click: unknown, Input: unknown, "
_DECLARATION = re.compile(r"^(\t*)Shared fields \[elements ([0-9]+(?:, [0-9]+)*)\]: Click: unknown; Input: unknown\Z")
_NODE = re.compile(r"^(\t*)([0-9]+) (.+)\Z")
_SCOPES = frozenset(("application", "window", "sheet", "menu", "menuBar"))


@dataclass(frozen=True)
class InputFieldsReplacement:
    """One unchanged SDK capability pair represented by its table declaration."""

    ref: str
    table: str
    root: str
    layout: tuple[tuple[str, str], ...]
    declaration: str
    original: str
    replacement: str
    fields: tuple[tuple[str, str], ...] = (("click", "unknown"), ("typeText", "unknown"))


@dataclass(frozen=True)
class InputFieldsLayout:
    """Changed public lines and the exact per-object fields shared in them."""

    lines: list[str]
    replacements: tuple[InputFieldsReplacement, ...] = ()


def _value_position(entry: NumberedEntry) -> int | None:
    """Find one explicit Value field without matching text inside its string."""
    outside = unquoted_positions(entry.body)
    positions = [
        match.start()
        for match in re.finditer(r"Value: ", entry.body)
        if match.start() in outside and match.start() > 0 and entry.body[match.start() - 1] == " "
    ]
    return positions[0] if len(positions) == 1 else None


def expand_input_fields(lines: list[str]) -> list[str]:
    """Restore the two fields for an explicit ref set inside its public table.

    Each declaration directly follows its numbered table. Its members must
    occur once, in the listed order, before that table's indentation scope ends.
    The original pair is inserted immediately before each explicit Value field.
    Malformed declarations remain literal rather than inventing missing objects.
    """
    entries = unique_numbered_entries(lines)
    replacements: dict[int, list[tuple[int, str]]] = defaultdict(list)
    removed: set[int] = set()
    claimed: set[str] = set()
    for index, line in enumerate(lines):
        match = _DECLARATION.fullmatch(line)
        if match is None or index == 0:
            continue
        table = _NODE.fullmatch(lines[index - 1])
        refs = match[2].split(", ")
        if (
            table is None
            or match[1] != table[1] + "\t"
            or len(refs) < 3
            or len(set(refs)) != len(refs)
            or claimed.intersection(refs)
        ):
            continue
        end = index + 1
        while end < len(lines) and lines[end].startswith(table[1] + "\t"):
            end += 1
        members = [entries.get(ref) for ref in refs]
        if any(item is None or not index < item.line < end for item in members):
            continue
        positions = [_value_position(item) for item in members]
        if any(
            position is None or item.body[:position].endswith(", ") or _FIELDS in item.body[:position]
            for item, position in zip(members, positions)
        ) or [(item.line, item.start) for item in members] != sorted((item.line, item.start) for item in members):
            continue
        for item, position in zip(members, positions):
            replacements[item.line].append((item.start + position, _FIELDS))
        claimed.update(refs)
        removed.add(index)
    output = []
    for index, line in enumerate(lines):
        if index in removed:
            continue
        for position, text in sorted(replacements.get(index, ()), reverse=True):
            line = line[:position] + text + line[position:]
        output.append(line)
    return output


def pack_input_fields(frame: LayoutFrame) -> InputFieldsLayout:
    """Share generated unknown fields without changing native capability facts.

    Only complete, named-field-free text inputs with the exact original pair
    immediately before Value qualify. Nearest table containment, original
    parents, layout scope, generated prefixes and current public slots agree.
    """
    if (
        not frame.context.get("compact")
        or frame.context.get("subtreeRef")
        or len(frame.roots) != 1
        or frame.roots[0]["role"] in ("menu", "menuBar")
    ):
        return InputFieldsLayout(frame.lines)
    nodes, parents, tables, order, counts = {}, {}, {}, [], Counter()
    pending = [(frame.roots[0], None, None, True)]
    while pending:
        node, parent, table, in_root = pending.pop()
        ref = node["ref"]
        counts[ref] += 1
        if counts[ref] > 1:
            return InputFieldsLayout(frame.lines)
        nodes[ref], parents[ref] = node, parent
        if parent is not None and node["role"] in _SCOPES:
            in_root = False
        if in_root and node["role"] in ("table", "outline"):
            table = ref
        elif not in_root or node["role"] in _SCOPES:
            table = None
        tables[ref] = table
        order.append(ref)
        pending.extend((child, ref, table, in_root) for child in reversed(node["children"]))
    records = {record.ref: record for record in frame.rendered_nodes}
    record_counts = Counter(record.ref for record in frame.rendered_nodes)
    entries = unique_numbered_entries(frame.lines)
    try:
        original_roles = unique_numbered_entries(expand_role_labels(frame.lines))
    except ValueError:
        return InputFieldsLayout(frame.lines)
    groups: dict[str, list[tuple[str, NumberedEntry, int]]] = defaultdict(list)
    for ref in order:
        node, record, entry = nodes[ref], records.get(ref), entries.get(ref)
        original_entry, prefix = original_roles.get(ref), frame.node_prefixes.get(ref)
        table = tables[ref]
        caps = node.get("capabilities")
        span = frame.node_line_ranges.get(ref)
        if (
            table is None
            or record is None
            or entry is None
            or original_entry is None
            or record_counts[ref] != 1
            or record.parent != parents[ref]
            or not prefix
            or span is None
            or span[1] - span[0] != 1
            or record.partial
            or record.hints
            or record.related
            or "\n" in record.text
            or has_incomplete_children(node)
            or node["children"]
            or node.get("name")
            or node["role"] not in ("textField", "textArea", "comboBox")
            or node.get("enabled") is False
            or node.get("blocked")
            or not isinstance(caps, Mapping)
            or caps.get("click") != "unknown"
            or caps.get("typeText") != "unknown"
            or caps.get("replaceText") == "available"
        ):
            continue
        start = ref + " " + prefix + " "
        expected = start + _FIELDS + "Value: "
        if not record.text.lstrip("\t").startswith(expected) or not original_entry.expanded.startswith(expected):
            continue
        position = _value_position(entry)
        if position is None or entry.body[max(0, position - len(_FIELDS)) : position] != _FIELDS:
            continue
        table_entry, table_record = entries.get(table), records.get(table)
        if (
            table_entry is None
            or table_record is None
            or record_counts[table] != 1
            or record.layout != table_record.layout
            or table_record.partial
            or has_incomplete_children(nodes[table])
            or table_record.parent != parents[table]
        ):
            continue
        ancestor, valid = ref, True
        while ancestor != table:
            original = records.get(ancestor)
            if (
                original is None
                or record_counts[ancestor] != 1
                or original.parent != parents[ancestor]
                or original.layout != table_record.layout
                or original.partial
                or has_incomplete_children(nodes[ancestor])
            ):
                valid = False
                break
            ancestor = parents[ancestor]
        if not valid:
            continue
        groups[table].append((ref, entry, position - len(_FIELDS)))
    lines = frame.lines.copy()
    changes: dict[int, list[tuple[int, int]]] = defaultdict(list)
    declarations: dict[int, str] = {}
    records_out: list[InputFieldsReplacement] = []
    root = frame.roots[0]["ref"]
    for table, members in groups.items():
        table_entry = entries[table]
        match = _NODE.fullmatch(frame.lines[table_entry.line])
        if (
            len(members) < 3
            or match is None
            or match[2] != table
            or [(entry.line, entry.start) for _, entry, _ in members]
            != sorted((entry.line, entry.start) for _, entry, _ in members)
        ):
            continue
        end = table_entry.line + 1
        while end < len(lines) and lines[end].startswith(match[1] + "\t"):
            end += 1
        if any(not table_entry.line < entry.line < end for _, entry, _ in members):
            continue
        declaration = (
            match[1]
            + "\tShared fields [elements "
            + ", ".join(ref for ref, _, _ in members)
            + "]: Click: unknown; Input: unknown"
        )
        if len(_FIELDS.encode("utf-8")) * len(members) - len((declaration + "\n").encode("utf-8")) < 8:
            continue
        declarations[table_entry.line + 1] = declaration
        for ref, entry, position in members:
            changes[entry.line].append((entry.start + position, len(_FIELDS)))
            replacement = entry.body[:position] + entry.body[position + len(_FIELDS) :]
            records_out.append(
                InputFieldsReplacement(ref, table, root, records[ref].layout, declaration, entry.body, replacement)
            )
    output = []
    for index, line in enumerate(lines):
        if index in declarations:
            output.append(declarations[index])
        for position, length in sorted(changes.get(index, ()), reverse=True):
            line = line[:position] + line[position + length :]
        output.append(line)
    if not records_out or expand_input_fields(output) != frame.lines:
        return InputFieldsLayout(frame.lines)
    return InputFieldsLayout(output, tuple(records_out))
