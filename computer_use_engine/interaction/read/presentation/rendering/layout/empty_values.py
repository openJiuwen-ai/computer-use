# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Pack empty table values without merging cell identities or coordinates."

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from computer_use.protocol.primitives import safe_integer


if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import NodeView
    from computer_use.protocol.records import Record


def compact_empty_values(lines: list[str], roots: Sequence[NodeView], focused_ref: str | None = None) -> list[str]:
    """Group eligible empty-value entries while retaining their individual references.

    Args:
        lines: Rendered observation lines, in their current display order.
        roots: Ordered roots in this observed or projected view.
        focusedRef: Observed focused display reference, or None when none was reported.
    """
    parents = {}
    empty_selected_refs = set()

    def collect(node: NodeView, parent: NodeView | None = None, in_table: bool = False) -> None:
        """Collect.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
            inTable: Whether the current source path is inside a table.
        """

        def eligible_empty_cell(node: NodeView, parent: NodeView | None, in_table: bool) -> bool:
            """Require each original projection condition in its original order."""
            if not (in_table and parent is not None and parent["role"] in ("table", "row", "column")):
                return False
            if not (node["role"] in ("cell", "textField") and node.get("value") == "" and not node["children"]):
                return False
            if not (node["ref"] != focused_ref and not node.get("focused") and not node.get("selected")):
                return False
            if not ("selectionRange" not in node and not node.get("selectedText") and "partialReason" not in node):
                return False
            if not (
                not node.get("labelTargets") and not node.get("incompleteLabelRelation") and not node.get("blockedBy")
            ):
                return False
            return True

        in_table = in_table or node["role"] == "table"
        if eligible_empty_cell(node, parent, in_table):
            parents[node["ref"]] = parent["ref"]
            if node.get("selectedText") == "":
                empty_selected_refs.add(node["ref"])
        for child in node["children"]:
            collect(child, node, in_table)

    for root in roots:
        collect(root)

    def cell(line: str) -> Record | None:
        match = re.match(
            (
                "^(\\t*)([0-9]+) (.+) ([A-Z]+)([1-9][0-9]*), ((?:(?:Click|Input): "
                "(?:available|unavailable|unknown), )*)Value: "
                '""(, Selected text: "")?\\Z'
            ),
            line,
        )
        if match is None or match[2] not in parents:
            return None
        indent_value, ref_value, properties_value, letters_value, row_text, fields_value, selection_value = (
            match.groups()
        )
        # Share only the exact selection fact already rendered for each source.
        # An absent/null report must not acquire another cell's known empty text.
        if selection_value and ref_value not in empty_selected_refs:
            return None
        column = 0
        for char in letters_value:
            column = column * 26 + ord(char) - 64
        row = int(row_text)
        if not safe_integer(column) or not safe_integer(row):
            return None
        return dict(
            ref=ref_value,
            address=letters_value + row_text,
            parent=parents[ref_value],
            indent=indent_value,
            properties=properties_value,
            fields=fields_value,
            selection=selection_value or "",
            column=column,
            row=row,
        )

    output, index = [], 0
    while index < len(lines):
        first = cell(lines[index])
        if first is None:
            output.append(lines[index])
            index += 1
            continue
        run, direction = [first], None
        while index + len(run) < len(lines):
            next_cell = cell(lines[index + len(run)])
            if next_cell is None or any(
                next_cell[k] != first[k] for k in ("parent", "indent", "properties", "fields", "selection")
            ):
                break
            previous = run[-1]
            horizontal = next_cell["row"] == previous["row"] and next_cell["column"] - previous["column"] == 1
            vertical = next_cell["column"] == previous["column"] and next_cell["row"] - previous["row"] == 1
            if not ((horizontal or vertical) if direction is None else horizontal if direction else vertical):
                break
            if direction is None:
                direction = horizontal
            run.append(next_cell)
        if len(run) >= 3:
            output.append(
                f"{first['indent']}Empty values [{first['properties']}; "
                f'{first["fields"]}Value: ""{first["selection"]}]: '
                + ", ".join(c["ref"] + " " + c["address"] for c in run)
            )
            index += len(run)
        else:
            output.append(lines[index])
            index += 1
    return output
