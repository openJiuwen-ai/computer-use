# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pack source-qualified empty cells using producer-owned presentation slots."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.protocol.primitives import safe_integer

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import NodeView


@dataclass(frozen=True)
class EmptyValueCell:
    """Source eligibility and coordinates, established before prose generation."""

    ref: str
    parent: str
    address: str
    column: int
    row: int
    caption: str = ""


@dataclass(frozen=True)
class EmptyValueLine:
    """Complete producer-generated slots for one still-unpacked cell line."""

    cell: EmptyValueCell
    indent: str
    properties: str
    fields: tuple[str, ...]
    text: str


def empty_value_cells(roots: Sequence[NodeView], focused_ref: str | None = None) -> dict[str, EmptyValueCell]:
    """Use source facts for eligibility; missing/unknown values never become empty."""
    cells = {}
    occurrences = Counter()
    pending = [(root, None, False) for root in roots]
    while pending:
        node, parent, in_table = pending.pop()
        ref = node["ref"]
        occurrences[ref] += 1
        in_table = in_table or node["role"] == "table"
        pending.extend((child, node, in_table) for child in node["children"])
        if not (
            in_table
            and parent is not None
            and parent["role"] in ("table", "row", "column")
            and node["role"] in ("cell", "textField")
            and node.get("value") == ""
            and not node["children"]
            and ref != focused_ref
            and not node.get("focused")
            and not node.get("selected")
            and "selectionRange" not in node
            and not node.get("selectedText")
            and "partialReason" not in node
            and not node.get("labelTargets")
            and not node.get("incompleteLabelRelation")
            and not node.get("blockedBy")
        ):
            continue
        # This parses only the actual source address, never a rendered label,
        # arbitrary metadata suffix, or a line's shape.
        name = node.get("name")
        match = re.fullmatch(r"(?:(.+) )?([A-Z]+)([1-9][0-9]*)", name) if isinstance(name, str) else None
        if match is None:
            continue
        column = 0
        for char in match[2]:
            column = column * 26 + ord(char) - 64
            if not safe_integer(column):
                break
        if not safe_integer(column) or len(match[3]) > 16:
            continue
        row = int(match[3])
        if safe_integer(row):
            cells[ref] = EmptyValueCell(ref, parent["ref"], match[2] + match[3], column, row, match[1] or "")
    return {ref: cell for ref, cell in cells.items() if occurrences[ref] == 1}


def compact_empty_values(lines: list[str], generated: Mapping[str, EmptyValueLine]) -> list[str]:
    """Share complete equal fields while retaining every original ref and address.

    Exact generated text locates records after preceding layout steps. It is
    never parsed to discover eligibility or fields; modified/multiline records
    stay explicit. New fields automatically remain in the shared header, and
    differing fields prevent grouping without another wording whitelist.
    """
    counts = Counter(lines)
    cells = {record.text: record for record in generated.values() if counts[record.text] == 1}
    output, index = [], 0
    while index < len(lines):
        first = cells.get(lines[index])
        if first is None:
            output.append(lines[index])
            index += 1
            continue
        run, direction = [first], None
        while index + len(run) < len(lines):
            following = cells.get(lines[index + len(run)])
            if following is None or following.cell.parent != first.cell.parent or following.indent != first.indent:
                break
            if following.properties != first.properties or following.fields != first.fields:
                break
            previous = run[-1].cell
            horizontal = following.cell.row == previous.row and following.cell.column - previous.column == 1
            vertical = following.cell.column == previous.column and following.cell.row - previous.row == 1
            if not ((horizontal or vertical) if direction is None else horizontal if direction else vertical):
                break
            if direction is None:
                direction = horizontal
            run.append(following)
        if len(run) >= 3:
            output.append(
                f"{first.indent}Empty values [{first.properties}; {', '.join(first.fields)}]: "
                + ", ".join(record.cell.ref + " " + record.cell.address for record in run)
            )
            index += len(run)
        else:
            output.append(lines[index])
            index += 1
    return output
