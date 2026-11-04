# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize the explicit field and row templates selected by geometric planning."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.text.columns import compact_grid, floating_row
from computer_use.interaction.read.presentation.interface.text.units import (
    capture_unit,
    write_control_unit,
    write_compound_control,
)
from typing import TYPE_CHECKING
from computer_use.interaction.read.presentation.interface.text.readable import write_heading

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceBlock
    from computer_use.interaction.read.presentation.interface.text.formatting import InterfaceWriter


def write_geometric_block(
    writer: InterfaceWriter, block: InterfaceBlock, depth: int, context: tuple[tuple[str, str], ...]
) -> bool:
    """Write a chosen geometric template and retain every original per-element record.

    Args:
        writer: Output accumulator and immutable selected content plans.
        block: Validated final layout; membership and placement are already selected.
        depth: Display indentation for this reading unit.
        context: Ancestor headings retained for incremental delivery.

    Returns:
        Whether the block used a geometric template. Other layouts continue through
        the ordinary serializer. No bounds, source ordering or grouping are inferred.
    """
    indent = "\t" * depth
    if block.layout == "source-only":
        if block.primary_ref is None or block.children:
            raise ValueError("A retained wrapper lacks its original leaf source")
        writer.write_atom(block.primary_ref, depth, context)
        return True
    if block.layout == "control-unit":
        write_control_unit(writer, block, depth, context)
        return True
    if block.layout == "compound-control":
        write_compound_control(writer, block, depth, context)
        return True
    if block.layout == "visual-row":
        cells = []
        trailing = []
        for key in block.children:
            main, annotations = capture_unit(writer, key, depth, context)
            cells.append(main)
            trailing.extend(annotations)
        if any(cells):
            writer.lines.append(indent + " | ".join(cell for cell in cells if cell))
        writer.lines.extend(trailing)
        return True
    if block.layout == "visual-grid":
        plan = next(
            (
                plan
                for plan in writer.document.geometric_patterns
                if plan.kind == "grid" and tuple(row.key for row in plan.rows) == block.children
            ),
            None,
        )
        if plan is not None:
            heading = indent + f"Visual grid ({plan.column_count} columns):"
            writer.lines.append(heading)
            context = (*context, (block.key, heading))
            depth += 1
            indent = "\t" * depth
        matrix, irregular, annotations = [], {}, {}
        for row_key in block.children:
            row = writer.document.blocks[row_key]
            cells = [""] * len(block.columns)
            placed, trailing = [], []
            for slot, key in zip(row.column_slots, row.children):
                main, tail = capture_unit(writer, key, depth, context)
                placed.append((slot, main))
                trailing.extend(tail)
                if slot is not None:
                    cells[slot] = main
            annotations[row_key] = tuple(trailing)
            if None in row.column_slots:
                irregular[row_key] = floating_row(placed, len(block.columns))
            else:
                matrix.append(cells)
        rendered = iter(compact_grid(matrix, block.column_alignments))
        for row_key in block.children:
            writer.lines.append(indent + (irregular[row_key] if row_key in irregular else next(rendered)))
            writer.lines.extend(annotations[row_key])
            writer.write_declarations(row_key, depth)
        return True
    if block.kind != "field" or block.primary_ref is not None or block.label_ref is None:
        return False
    if block.layout not in ("fields", "field-inline") or writer.document.format_version != "geometric-2":
        return False
    ref = block.label_ref
    heading_index = len(writer.lines)
    readable_heading = write_heading(writer, block, depth, context)
    transparent = readable_heading == ""
    if readable_heading is not None:
        heading = readable_heading
    elif block.heading_style == "caption":
        writer.remember(ref, depth, context)
        atom = writer.atoms[ref]
        heading = indent + ref + " " + writer.value(ref, atom.slots[0]) + ":"
        writer.lines.append(heading)
    else:
        heading = writer.write_field_heading(ref, depth, context)
        heading += ":"
        writer.lines[heading_index] += ":"
    nested_context = context if transparent else (*context, (block.key, heading))
    content = []
    for key in block.children:
        member = writer.document.blocks[key]
        if member.primary_ref == ref:
            writer.write_declarations(key, depth)
        else:
            content.append(key)
    for index, key in enumerate(content):
        start = len(writer.lines)
        writer.write(key, depth if transparent else depth + 1, nested_context)
        if index == 0 and block.layout == "field-inline" and not transparent:
            if start == len(writer.lines):
                continue
            writer.lines[heading_index] += " " + writer.lines.pop(start).lstrip("\t")
    return True
