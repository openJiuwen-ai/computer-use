# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reversible four-column layout for complete row/single-cell records."""

from __future__ import annotations

from dataclasses import dataclass
import re

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.protocol.primitives import utf16_length
from computer_use.interaction.read.presentation.rendering.layout.record_lines import (
    GeneratedLine as _Line,
    generated_lines as _lines,
    point_position as _point,
)

_NUMBER = r"(?:0|[1-9][0-9]*)"
_NODE = re.compile(r"^(\t*)(" + _NUMBER + r") (.+)\Z")
_TITLE = (
    "Records: each row contains its cell; the last column lists displayed "
    "cell children in order. Object entries begin with refs."
)
_HEADER = re.compile(
    r"row (Index(?: \(0-based\))?) / cell Row \| row \| "
    r"([^|;\r\n]+), Column: (" + _NUMBER + r") \| cell children \(—: none\)\Z"
)


@dataclass(frozen=True)
class _Row:
    """One exact record and the shared field meanings for its four columns."""

    start: int
    end: int
    parent: str
    position: int
    indent: str
    layout: tuple[tuple[str, str], ...]
    index_label: str
    cell_prefix: str
    column: int
    text: str


def _caption_has_name(caption: str) -> bool:
    if caption.startswith("("):
        state = re.fullmatch(r"\([^()]*\)(?: (.*))?", caption)
        return state is not None and bool(state[1])
    return bool(caption)


def _expand_member(text: str, indent: str, index_label: str, cell_prefix: str, column: str) -> list[str] | None:
    """Recover the original numbered lines solely from one public table row."""
    fields = text.split(" | ")
    if len(fields) != 4:
        return None
    position, row, cell, children = fields
    indices = re.fullmatch("(" + _NUMBER + r") / (" + _NUMBER + ")", position)
    row_match = re.fullmatch("(" + _NUMBER + r")(?: (.*?))?(?:, Outline level: (" + _NUMBER + "))?", row)
    cell_match = re.fullmatch("(" + _NUMBER + r")(?: (.*))?", cell)
    if indices is None or row_match is None or cell_match is None:
        return None
    caption = row_match[2] or ""
    result = [
        indent
        + row_match[1]
        + " row"
        + (" " + caption if caption else "")
        + (", " if _caption_has_name(caption) else " ")
        + index_label
        + ": "
        + indices[1]
        + (", Outline level: " + row_match[3] if row_match[3] else "")
    ]
    result.append(
        indent
        + "\t"
        + cell_match[1]
        + " "
        + cell_prefix
        + (" " + cell_match[2] + "," if cell_match[2] else "")
        + " Row: "
        + indices[2]
        + ", Column: "
        + column
    )
    if children != "—":
        for child in children.split("; "):
            if not re.fullmatch(_NUMBER + " .+", child):
                return None
            result.append(indent + "\t\t" + child)
    return result


def expand_row_records(lines: list[str]) -> list[str]:
    """Expand public four-column records without consulting native facts.

    Role/state and Column belong to each cell; Index belongs to its row. Every
    row, cell and displayed child retains its own reference and original order.
    Unrecognized or incomplete tables remain literal.
    """
    result: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].lstrip("\t") != _TITLE or index + 1 >= len(lines):
            result.append(lines[index])
            index += 1
            continue
        indent = lines[index][: -len(_TITLE)]
        header = _HEADER.fullmatch(lines[index + 1][len(indent) :]) if lines[index + 1].startswith(indent) else None
        if header is None:
            result.append(lines[index])
            index += 1
            continue
        end = index + 2
        expanded: list[str] = []
        members = 0
        while end < len(lines) and re.match(re.escape(indent) + _NUMBER + r" / " + _NUMBER + r" \| ", lines[end]):
            restored = _expand_member(lines[end][len(indent) :], indent, header[1], header[2], header[3])
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


def _row(row: _Line, parent: str, position: int, lines: dict[str, _Line], frame: LayoutFrame) -> _Row | None:
    """Extract only the exact table fields already proven by the projection."""
    node = row.node
    if (
        node["role"] != "row"
        or len(node["children"]) != 1
        or not (row.prefix == "row" or row.prefix.startswith("row ("))
    ):
        return None
    child = node["children"][0]
    cell = lines.get(child["ref"])
    if (
        cell is None
        or child["role"] != "cell"
        or cell.record.parent != node["ref"]
        or cell.indent != row.indent + "\t"
        or cell.record.layout != row.record.layout
        or cell.index != row.index + 1
    ):
        return None
    index = node.get("index")
    cell_row, column = _point(child, "rowRange"), _point(child, "columnRange")
    if type(index) is not int or index < 0 or cell_row is None or column is None:
        return None
    name = node.get("name")
    if name and (not isinstance(name, str) or name.startswith("(")):
        return None
    row_name = frame.format_field(name) if name else ""
    stem = row.prefix + (" " + row_name if row_name else "")
    separator = ", " if row_name else " "
    row_fields = re.fullmatch(
        re.escape(stem + separator)
        + r"(Index(?: \(0-based\))?): "
        + str(index)
        + r"(, Outline level: ("
        + _NUMBER
        + r"))?",
        row.body,
    )
    if row_fields is None:
        return None
    if row_fields[3] is not None and (
        type(node.get("disclosureLevel")) is not int or node["disclosureLevel"] != int(row_fields[3])
    ):
        return None
    cell_name = frame.format_field(child["name"]) if child.get("name") else ""
    expected = cell.prefix + (" " + cell_name + "," if cell_name else "") + f" Row: {cell_row}, Column: {column}"
    if cell.body != expected:
        return None
    children: list[str] = []
    for offset, grandchild in enumerate(child["children"], 2):
        item = lines.get(grandchild["ref"])
        if (
            item is None
            or grandchild["children"]
            or item.record.parent != child["ref"]
            or item.indent != row.indent + "\t\t"
            or item.index != row.index + offset
            or item.record.layout != row.record.layout
        ):
            return None
        children.append(grandchild["ref"] + " " + item.body)
    end = row.index + 2 + len(children)
    if end < len(frame.lines) and frame.lines[end].startswith(row.indent + "\t"):
        return None
    row_body = node["ref"] + stem[len("row") :] + (row_fields[2] or "")
    cell_body = child["ref"] + (" " + cell_name if cell_name else "")
    text = " | ".join((f"{index} / {cell_row}", row_body, cell_body, "; ".join(children) or "—"))
    if utf16_length(text) > 320:
        return None
    return _Row(
        row.index, end, parent, position, row.indent, row.record.layout, row_fields[1], cell.prefix, column, text
    )


def pack_row_records(frame: LayoutFrame) -> list[str]:
    """Share table fields for consecutive complete row/single-cell records.

    Only current single-line source bodies participate. All children remain
    independent numbered objects, and malformed, partial or already-packed
    records retain their tree layout. The public decoder must recover every
    input line exactly before this final compact-layout transform is accepted.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef"):
        return frame.lines
    lines, positions = _lines(frame)
    candidates: dict[int, _Row] = {}
    for ref, row in lines.items():
        position = positions.get(ref)
        if position is not None and row.record.parent == position[0]:
            candidate = _row(row, *position, lines, frame)
            if candidate is not None:
                candidates[candidate.start] = candidate
    output: list[str] = []
    index = 0
    while index < len(frame.lines):
        first = candidates.get(index)
        if first is None:
            output.append(frame.lines[index])
            index += 1
            continue
        members = [first]
        while current := candidates.get(members[-1].end):
            if (
                current.parent != first.parent
                or current.position != first.position + len(members)
                or current.indent != first.indent
                or current.layout != first.layout
                or current.index_label != first.index_label
                or current.cell_prefix != first.cell_prefix
                or current.column != first.column
            ):
                break
            members.append(current)
        packed = [
            first.indent + _TITLE,
            first.indent
            + "row "
            + first.index_label
            + " / cell Row | row | "
            + first.cell_prefix
            + ", Column: "
            + str(first.column)
            + " | cell children (—: none)",
            *(first.indent + member.text for member in members),
        ]
        original = frame.lines[index : members[-1].end]
        saving = len("\n".join(original).encode("utf-8", errors="surrogatepass")) - len(
            "\n".join(packed).encode("utf-8", errors="surrogatepass")
        )
        if len(members) >= 3 and saving >= 8 and expand_row_records(packed) == original:
            output.extend(packed)
            index = members[-1].end
        else:
            output.extend(frame.lines[index : first.end])
            index = first.end
    return output if expand_row_records(output) == frame.lines else frame.lines
