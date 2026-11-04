# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize complete inline units while retaining their original source records."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.interface.geometric.units_support import inline_unit_refs
from computer_use.interaction.read.presentation.interface.text.readable import notes_only

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceBlock
    from computer_use.interaction.read.presentation.interface.text.formatting import InterfaceWriter


def capture_unit(
    writer: InterfaceWriter, key: str, depth: int, context: tuple[tuple[str, str], ...]
) -> tuple[str, tuple[str, ...]]:
    """Render a preselected unit once, separating its main line from annotations.

    Args:
        writer: The shared serializer owning source records and declarations.
        key: An atomic object or a frozen, complete control-unit block.
        depth: Display depth used for the unit's independent diff records.
        context: Existing ancestor headings for incremental delivery.

    Returns:
        The unit's main line without outer indentation, and its remaining lines
        with their prepared indentation. No geometry or content grouping occurs.
    """
    start = len(writer.lines)
    writer.write(key, depth, context)
    lines = tuple(writer.lines[start:])
    del writer.lines[start:]
    if not lines:
        block = writer.document.blocks[key]
        if block.primary_ref is not None and notes_only(writer, block.primary_ref):
            return "", ()
        raise ValueError("A selected inline unit produced no source content")
    return lines[0].removeprefix("\t" * depth), lines[1:]


def write_control_unit(
    writer: InterfaceWriter, block: InterfaceBlock, depth: int, context: tuple[tuple[str, str], ...]
) -> None:
    """Write complete control and caption atoms when no content-composition plan applies.

    The normal content planner may already provide a composed field. This path
    retains the complete original atoms for standalone layout consumers, including
    captions with independent actions. All contained declaration scopes remain live.
    """
    refs = inline_unit_refs(block.key, writer.document.blocks)
    if refs is None:
        raise ValueError("A control unit lost its original source inventory")
    _write_bound_sources(writer, block, refs, depth, context)


def write_compound_control(
    writer: InterfaceWriter, block: InterfaceBlock, depth: int, context: tuple[tuple[str, str], ...]
) -> None:
    """Serialize a primary control and each independent coincident source atom."""
    members = tuple(writer.document.blocks[key] for key in block.children)
    refs = tuple(member.primary_ref for member in members if member.primary_ref is not None)
    if len(refs) != len(members) or any(member.children for member in members) or len(refs) != len(set(refs)):
        raise ValueError("A compound control lost its flat independent source atoms")
    _write_bound_sources(writer, block, refs, depth, context)


def _write_bound_sources(
    writer: InterfaceWriter,
    block: InterfaceBlock,
    refs: tuple[str, ...],
    depth: int,
    context: tuple[tuple[str, str], ...],
) -> None:
    """Keep one visual control's primary and auxiliary source records together."""
    target = block.field_target_ref
    if target not in refs:
        raise ValueError("A control unit lost its original target or caption source")
    indent = "\t" * depth
    writer.remember(target, depth, context)
    main, *tail = writer.atom_lines(target, shared=True)
    trailing = [indent + line for line in tail]
    for ref in refs:
        if ref == target:
            continue
        writer.remember(ref, depth + 1, context)
        if notes_only(writer, ref):
            continue
        head, *tail = writer.atom_lines(ref, shared=True)
        main += " [" + head + "]"
        trailing.extend(indent + "\t" + line for line in tail)
    writer.lines.append(indent + main)
    writer.lines.extend(trailing)
    for ref in refs:
        if writer.request.coverage is None or ref not in writer.request.coverage.omitted_refs:
            writer.lines.extend(indent + "\t" + line for line in writer.coverage_lines(writer.atoms[ref]))
    pending = list(reversed(block.children))
    while pending:
        key = pending.pop()
        writer.write_declarations(key, depth)
        pending.extend(reversed(writer.document.blocks[key].children))
