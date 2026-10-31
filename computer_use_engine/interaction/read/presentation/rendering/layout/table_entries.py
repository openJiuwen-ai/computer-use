# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share identical table-reading instructions inside one public root."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.multi_column_records import expand_multi_column_records
from computer_use.interaction.read.presentation.rendering.layout.public_entries import unique_numbered_entries

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame, TableLayoutEvidence

_DECLARATION = "Table entries in this root: "
_GRAMMAR = "row-ref (Index); cell-ref > child; siblings use ;."
_OWN_GRAMMAR = "row-ref (Index); cell-ref with its own text > child; siblings use ;."
_NODE = re.compile(r"^(\t*)([0-9]+) (.+)\Z")
_INTRO = re.compile(r"^(\t*)Rows: [^;\r\n]+, Outline level: [0-9]+; cells: [^;\r\n]+\.(?: Entries: (.+))?\Z")


@dataclass(frozen=True)
class TableEntriesReplacement:
    """One generated introduction represented by the root's shared sentence."""

    table: str
    root: str
    layout: tuple[tuple[str, str], ...]
    rows: tuple[str, ...]
    original: str
    replacement: str
    declaration: str


@dataclass(frozen=True)
class TableEntriesLayout:
    """Public text and only the introductions that actually changed."""

    lines: list[str]
    replacements: tuple[TableEntriesReplacement, ...] = ()


def _block(lines: list[str], index: int, grammar: str) -> tuple[int, tuple[str, ...]] | None:
    """Validate a full public table before shortening or expanding its sentence."""
    intro = _INTRO.fullmatch(lines[index])
    if intro is None or index + 5 >= len(lines):
        return None
    indent = intro[1]
    end = index + 3
    rows: list[str] = []
    while end < len(lines):
        member = re.match(re.escape(indent) + r"[0-9]+ \| ([0-9]+) \([0-9]+\) \| ", lines[end])
        if member is None:
            break
        rows.append(member[1])
        end += 1
    block = lines[index:end]
    if intro[2] is None:
        block = [block[0] + " Entries: " + grammar, *block[1:]]
    if len(rows) < 3 or len(set(rows)) != len(rows) or expand_multi_column_records(block) == block:
        return None
    return end, tuple(rows)


def expand_table_entries(lines: list[str]) -> list[str]:
    """Restore table introductions from a self-contained root-local sentence.

    The declaration applies only to validated Rows blocks below its immediately
    following numbered root. A peer root, menu or root-level note ends it.
    Ambiguous declarations remain literal, allowing a producer to reject them
    with an exact round-trip comparison.
    """
    output: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip("\t")
        indent = lines[index][: len(lines[index]) - len(stripped)]
        if not stripped.startswith(_DECLARATION):
            output.append(lines[index])
            index += 1
            continue
        grammar = stripped[len(_DECLARATION) :]
        root = _NODE.fullmatch(lines[index + 1]) if index + 1 < len(lines) else None
        if (
            grammar not in (_GRAMMAR, _OWN_GRAMMAR)
            or root is None
            or root[1] != indent
            or index > 0
            and lines[index - 1].lstrip("\t").startswith(_DECLARATION)
        ):
            output.append(lines[index])
            index += 1
            continue
        end = index + 2
        while end < len(lines) and lines[end].startswith(indent + "\t"):
            end += 1
        restored = lines[index + 1 : end].copy()
        changed = 0
        valid = True
        for offset, line in enumerate(restored):
            intro = _INTRO.fullmatch(line)
            if intro is None:
                continue
            if intro[2] is not None or _block(restored, offset, grammar) is None:
                valid = False
                break
            restored[offset] += " Entries: " + grammar
            changed += 1
        if valid and changed >= 2:
            output.extend(restored)
            index = end
        else:
            output.append(lines[index])
            index += 1
    return output


def pack_table_entries(frame: LayoutFrame) -> TableEntriesLayout:
    """Share only introductions produced by the multi-column layout stage.

    Role/state labels, outline levels and row/cell data remain local to each
    table. Generated evidence and current public member order must both match.
    """
    if (
        not frame.context.get("compact")
        or frame.context.get("subtreeRef")
        or len(frame.roots) != 1
        or frame.roots[0]["role"] in ("menu", "menuBar")
        or len(frame.table_layouts) < 2
    ):
        return TableEntriesLayout(frame.lines)
    root = frame.roots[0]["ref"]
    entries = unique_numbered_entries(frame.lines)
    root_entry = entries.get(root)
    if root_entry is None:
        return TableEntriesLayout(frame.lines)
    root_match = _NODE.fullmatch(frame.lines[root_entry.line])
    if root_match is None or root_match[2] != root:
        return TableEntriesLayout(frame.lines)
    root_end = root_entry.line + 1
    while root_end < len(frame.lines) and frame.lines[root_end].startswith(root_match[1] + "\t"):
        root_end += 1
    records = {record.ref: record for record in frame.rendered_nodes}
    counts = Counter(record.ref for record in frame.rendered_nodes)
    nodes, parents = {}, {}
    pending = [(frame.roots[0], None)]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        if ref in nodes:
            return TableEntriesLayout(frame.lines)
        nodes[ref], parents[ref] = node, parent
        pending.extend((child, ref) for child in node["children"])
    candidates: list[tuple[int, TableLayoutEvidence, str]] = []
    for evidence in frame.table_layouts:
        table = entries.get(evidence.table)
        record = records.get(evidence.table)
        if table is None or record is None or counts[evidence.table] != 1 or record.layout != evidence.layout:
            return TableEntriesLayout(frame.lines)
        ancestor = evidence.table
        while ancestor != root:
            source, original = nodes.get(ancestor), records.get(ancestor)
            if (
                source is None
                or original is None
                or counts[ancestor] != 1
                or original.parent != parents[ancestor]
                or source["role"] in ("application", "window", "sheet", "menu", "menuBar")
            ):
                return TableEntriesLayout(frame.lines)
            ancestor = parents[ancestor]
        matches = [i for i in range(table.line + 1, root_end) if frame.lines[i] == evidence.introduction]
        # The nearest containing object remains the original table. Repeated
        # identical introductions are resolved by their exact ordered row refs.
        matches = [
            i
            for i in matches
            if (block := _block(frame.lines, i, _GRAMMAR))
            and block[1] == evidence.rows
            and frame.lines[i + 1] == evidence.heading
        ]
        if len(matches) != 1:
            return TableEntriesLayout(frame.lines)
        index = matches[0]
        intro = _INTRO.fullmatch(evidence.introduction)
        if (
            intro is None
            or intro[2] not in (_GRAMMAR, _OWN_GRAMMAR)
            or not root_entry.line <= table.line < index < root_end
            or len(intro[1]) != len(frame.lines[table.line]) - len(frame.lines[table.line].lstrip("\t")) + 1
            or any(not line.startswith(intro[1]) for line in frame.lines[table.line + 1 : index])
        ):
            return TableEntriesLayout(frame.lines)
        candidates.append((index, evidence, intro[2]))
    if len({grammar for _, _, grammar in candidates}) != 1 or len({i for i, _, _ in candidates}) != len(candidates):
        return TableEntriesLayout(frame.lines)
    grammar = candidates[0][2]
    declaration = root_match[1] + _DECLARATION + grammar
    lines = frame.lines.copy()
    replacements = []
    for index, evidence, _ in candidates:
        replacement = evidence.introduction.removesuffix(" Entries: " + grammar)
        lines[index] = replacement
        replacements.append(
            TableEntriesReplacement(
                evidence.table, root, evidence.layout, evidence.rows, evidence.introduction, replacement, declaration
            )
        )
    lines.insert(root_entry.line, declaration)
    saving = len("\n".join(frame.lines).encode("utf-8", errors="surrogatepass")) - len(
        "\n".join(lines).encode("utf-8", errors="surrogatepass")
    )
    if saving < 8 or expand_table_entries(lines) != frame.lines:
        return TableEntriesLayout(frame.lines)
    return TableEntriesLayout(lines, tuple(replacements))
