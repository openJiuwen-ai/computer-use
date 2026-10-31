# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reversible tables for complete native rows with several simple cells."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
import re

from computer_use.interaction.read.presentation.rendering.layout.leaf_headers import expand_leaf_headers
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame, TableLayoutEvidence
from computer_use.interaction.read.presentation.rendering.layout.record_lines import (
    GeneratedLine,
    generated_lines,
    point_position,
)
from computer_use.protocol.primitives import utf16_length

_NUMBER = r"(?:0|[1-9][0-9]*)"
_NODE = re.compile(r"^(\t*)(" + _NUMBER + r") (.+)\Z")
_INTRO = re.compile(
    r"^(\t*)Rows: ([^;\r\n]+), Outline level: (" + _NUMBER + r"); cells: ([^;\r\n]+)\. Entries: row-ref \(Index\); "
    r"cell-ref( with its own text)? > child; siblings use ;\.\Z"
)
# Tables stay readable when each contained branch is short but their sum is not.
_MAX_CELL_UNITS = 320
_MAX_ROW_UNITS = 640
_MAX_CELL_BRANCHES = 6


@dataclass(frozen=True)
class _Row:
    """One complete record and the exact fields eligible for local sharing."""

    start: int
    end: int
    parent: str
    position: int
    indent: str
    layout: tuple[tuple[str, str], ...]
    row_prefix: str
    index_label: str
    outline: int
    cell_prefix: str
    columns: int
    text: str
    cell_text: bool

    @property
    def shared(self) -> tuple[object, ...]:
        """Return fields whose meaning must match throughout a public table."""
        return (
            self.parent,
            self.indent,
            self.layout,
            self.row_prefix,
            self.index_label,
            self.outline,
            self.cell_prefix,
            self.columns,
        )


def _outline_row(table: GeneratedLine, row: GeneratedLine, frame: LayoutFrame) -> bool:
    """Require unchanged native row membership before tabulating an outline."""
    proof = frame.row_collections.get(table.node["ref"])
    original = table.node.get("native")
    original_row = row.node.get("native")
    return (
        proof is not None
        and row.node["ref"] in proof.rows
        and isinstance(original, Mapping)
        and isinstance(original_row, Mapping)
        and tuple(child["ref"] for child in original["children"]) == proof.children
        and tuple(child["ref"] for child in original_row["children"]) == proof.cells.get(row.node["ref"])
        and tuple(child["ref"] for child in row.node["children"]) == proof.cells.get(row.node["ref"])
    )


def _cell_body(cell: GeneratedLine, row: int, column: int) -> str | None:
    """Keep the complete own body before exact generated trailing coordinates."""
    coordinates = f"Row: {row}, Column: {column}"
    if cell.body == cell.prefix + " " + coordinates:
        return "" if not cell.node.get("name") else None
    prefix, suffix = cell.prefix + " ", ", " + coordinates
    if cell.body.startswith(prefix) and cell.body.endswith(suffix):
        own = cell.body[len(prefix) : -len(suffix)]
        if own and " > " not in own:
            return own
    return None


def _branch(
    item: GeneratedLine, parent: GeneratedLine, expected_index: int, lines: dict[str, GeneratedLine], depth: int = 0
) -> tuple[str, int] | None:
    """Retain one short single-child chain with every original object body."""
    if (
        item.record.parent != parent.node["ref"]
        or item.indent != parent.indent + "\t"
        or item.index != expected_index
        or item.record.layout != parent.record.layout
        or len(item.indent) - item.record.depth != len(parent.indent) - parent.record.depth
        or len(item.node["children"]) > 1
        or depth > 2
        or " > " in item.body
    ):
        return None
    text = item.node["ref"] + " " + item.body
    end = item.index + 1
    if item.node["children"]:
        child = lines.get(item.node["children"][0]["ref"])
        if child is None:
            return None
        nested = _branch(child, item, end, lines, depth + 1)
        if nested is None:
            return None
        text += " > " + nested[0]
        end = nested[1]
    return text, end


def _row(
    item: GeneratedLine, parent: str, position: int, lines: dict[str, GeneratedLine], frame: LayoutFrame
) -> _Row | None:
    """Use typed row positions and exact generated fields without inferring values."""
    node = item.node
    table = lines.get(parent)
    index, outline = node.get("index"), node.get("disclosureLevel")
    if (
        table is None
        or (table.node["role"] != "table" and (table.node["role"] != "outline" or not _outline_row(table, item, frame)))
        or node["role"] != "row"
        or item.record.parent != parent
        or item.record.layout != table.record.layout
        or item.indent != table.indent + "\t"
        or not 2 <= len(node["children"]) <= 5
        or len(item.indent) - item.record.depth != len(table.indent) - table.record.depth
        or node.get("name")
        or type(index) is not int
        or index < 0
        or type(outline) is not int
        or outline < 0
    ):
        return None
    fields = re.fullmatch(
        re.escape(item.prefix) + r" (Index(?: \(0-based\))?): " + str(index) + ", Outline level: " + str(outline),
        item.body,
    )
    if fields is None:
        return None
    cells: list[str] = []
    prefixes: set[str] = set()
    roles: set[str] = set()
    row_numbers: set[int] = set()
    has_cell_text = False
    end = item.index + 1
    for column, child in enumerate(node["children"], 1):
        cell = lines.get(child["ref"])
        row_number = point_position(child, "rowRange")
        if (
            cell is None
            or child["role"] not in ("cell", "columnHeader")
            or row_number is None
            or len(child["children"]) > _MAX_CELL_BRANCHES
            or point_position(child, "columnRange") != column
            or cell.record.parent != node["ref"]
            or cell.record.layout != item.record.layout
            or cell.indent != item.indent + "\t"
            or cell.index != end
            or len(cell.indent) - cell.record.depth != len(item.indent) - item.record.depth
        ):
            return None
        own = _cell_body(cell, row_number, column)
        if own is None:
            return None
        has_cell_text |= bool(own)
        prefixes.add(cell.prefix)
        roles.add(child["role"])
        row_numbers.add(row_number)
        contents: list[str] = []
        end += 1
        for descendant in child["children"]:
            entry = lines.get(descendant["ref"])
            branch = _branch(entry, cell, end, lines) if entry is not None else None
            if branch is None:
                return None
            contents.append(branch[0])
            end = branch[1]
        cell_text = child["ref"] + (" " + own if own else "") + (" > " + " ; ".join(contents) if contents else "")
        if utf16_length(cell_text) > _MAX_CELL_UNITS:
            return None
        cells.append(cell_text)
    if (
        len(prefixes) != 1
        or len(roles) != 1
        or len(row_numbers) != 1
        or end < len(frame.lines)
        and frame.lines[end].startswith(item.indent + "\t")
    ):
        return None
    text = " | ".join((str(next(iter(row_numbers))), f"{node['ref']} ({index})", *cells))
    if utf16_length(text) > _MAX_ROW_UNITS:
        return None
    return _Row(
        item.index,
        end,
        parent,
        position,
        item.indent,
        item.record.layout,
        item.prefix,
        fields[1],
        outline,
        next(iter(prefixes)),
        len(cells),
        text,
        has_cell_text,
    )


def _expand_member(
    text: str,
    indent: str,
    row_prefix: str,
    index_label: str,
    outline: str,
    cell_prefix: str,
    columns: int,
    own_text: bool,
) -> list[str] | None:
    """Restore one public record's original rows, cells and contained chains."""
    parts = text.split(" | ")
    if len(parts) != columns + 2 or re.fullmatch(_NUMBER, parts[0]) is None:
        return None
    row = re.fullmatch("(" + _NUMBER + r") \((" + _NUMBER + r")\)", parts[1])
    if row is None:
        return None
    result = [f"{indent}{row[1]} {row_prefix} {index_label}: {row[2]}, Outline level: {outline}"]
    for column, entry in enumerate(parts[2:], 1):
        cell, separator, descendants = entry.partition(" > ")
        own = re.fullmatch("(" + _NUMBER + r")(?: (.+))?", cell)
        if own is None or own[2] and not own_text:
            return None
        body = own[2] + ", " if own[2] else ""
        result.append(f"{indent}\t{own[1]} {cell_prefix} {body}Row: {parts[0]}, Column: {column}")
        if separator:
            for branch in descendants.split(" ; "):
                for depth, child in enumerate(branch.split(" > "), 2):
                    if re.fullmatch(_NUMBER + " .+", child) is None:
                        return None
                    result.append(indent + "\t" * depth + child)
    return result


def expand_multi_column_records(lines: list[str]) -> list[str]:
    """Recover node lines using only public table headings and member entries.

    Shared fields belong to the explicit row and cell objects. Every descendant
    retains its own reference, body and position within its original cell.
    Malformed or incomplete table declarations remain literal.
    """
    result: list[str] = []
    index = 0
    while index < len(lines):
        intro = _INTRO.fullmatch(lines[index])
        if intro is None or index + 2 >= len(lines):
            result.append(lines[index])
            index += 1
            continue
        indent, row_prefix, outline, cell_prefix, own_text = intro.groups()
        heading = lines[index + 1]
        columns = heading[len(indent) :].split(" | ") if heading.startswith(indent) else []
        index_label = columns[1][5:-1] if len(columns) >= 2 and columns[1].startswith("row (") else ""
        if (
            not 4 <= len(columns) <= 7
            or columns[0] != "Row"
            or index_label not in ("Index", "Index (0-based)")
            or columns[1] != f"row ({index_label})"
            or columns[2:] != [f"Column {n}" for n in range(1, len(columns) - 1)]
            or lines[index + 2] != indent + " | ".join(["---"] * len(columns))
        ):
            result.append(lines[index])
            index += 1
            continue
        end = index + 3
        expanded: list[str] = []
        members = 0
        while end < len(lines) and re.match(re.escape(indent) + _NUMBER + r" \| ", lines[end]):
            restored = _expand_member(
                lines[end][len(indent) :],
                indent,
                row_prefix,
                index_label,
                outline,
                cell_prefix,
                len(columns) - 2,
                bool(own_text),
            )
            if restored is None:
                break
            expanded.extend(restored)
            members += 1
            end += 1
        refs = [match[2] for line in expanded if (match := _NODE.fullmatch(line))]
        if members >= 3 and len(refs) == len(set(refs)):
            result.extend(expanded)
            index = end
        else:
            result.append(lines[index])
            index += 1
    return result


@dataclass(frozen=True)
class MultiColumnLayout:
    """Packed lines and the exact table declarations actually produced."""

    lines: list[str]
    tables: tuple[TableLayoutEvidence, ...] = ()


def pack_multi_column_layout(frame: LayoutFrame) -> MultiColumnLayout:
    """Arrange consecutive complete multi-cell rows as a reversible text table.

    The typed projection proves containment and each logical position. Exact
    generated bodies prove what the shared headings replace. Structured nodes,
    native bindings and individual diff entries remain unchanged.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef"):
        return MultiColumnLayout(frame.lines)
    expanded = expand_leaf_headers(frame.lines)
    working = replace(frame, lines=expanded)
    lines, positions = generated_lines(working, allow_reindent=True)
    original_positions: dict[str, list[int]] = defaultdict(list)
    for index, line in enumerate(frame.lines):
        original_positions[line].append(index)
    candidates: dict[int, _Row] = {}
    for ref, item in lines.items():
        position = positions.get(ref)
        if position is not None:
            record = _row(item, *position, lines, working)
            if record is not None:
                starts = original_positions[expanded[record.start]]
                if len(starts) != 1:
                    continue
                start = starts[0]
                end = start + 1
                while end < len(frame.lines) and frame.lines[end].startswith(record.indent + "\t"):
                    end += 1
                # A row's original public range must independently expand to the
                # entire proven candidate; unrelated shared titles stay intact.
                if expand_leaf_headers(frame.lines[start:end]) == expanded[record.start : record.end]:
                    candidates[start] = replace(record, start=start, end=end)
    output: list[str] = []
    tables: list[TableLayoutEvidence] = []
    index = 0
    while index < len(frame.lines):
        first = candidates.get(index)
        if first is None:
            output.append(frame.lines[index])
            index += 1
            continue
        members = [first]
        while current := candidates.get(members[-1].end):
            if current.shared != first.shared or current.position != first.position + len(members):
                break
            members.append(current)
        packed = [
            first.indent + f"Rows: {first.row_prefix}, Outline level: {first.outline}; "
            f"cells: {first.cell_prefix}. Entries: row-ref (Index); cell-ref"
            + (" with its own text" if any(member.cell_text for member in members) else "")
            + " > child; siblings use ;.",
            first.indent
            + " | ".join(("Row", f"row ({first.index_label})", *(f"Column {n}" for n in range(1, first.columns + 1)))),
            first.indent + " | ".join(["---"] * (first.columns + 2)),
            *(first.indent + member.text for member in members),
        ]
        original = frame.lines[index : members[-1].end]
        saving = len("\n".join(original).encode("utf-8", errors="surrogatepass")) - len(
            "\n".join(packed).encode("utf-8", errors="surrogatepass")
        )
        if len(members) >= 3 and saving >= 8 and expand_multi_column_records(packed) == expand_leaf_headers(original):
            tables.append(
                TableLayoutEvidence(
                    first.parent,
                    first.layout,
                    packed[0],
                    packed[1],
                    tuple(member.text.split(" | ", 2)[1].split(" ", 1)[0] for member in members),
                )
            )
            output.extend(packed)
            index = members[-1].end
        else:
            output.extend(frame.lines[index : first.end])
            index = first.end
    if expand_leaf_headers(expand_multi_column_records(output)) == expanded:
        return MultiColumnLayout(output, tuple(tables))
    return MultiColumnLayout(frame.lines)


def pack_multi_column_records(frame: LayoutFrame) -> list[str]:
    """Return the public table lines; typed publication uses the layout result."""
    return pack_multi_column_layout(frame).lines
