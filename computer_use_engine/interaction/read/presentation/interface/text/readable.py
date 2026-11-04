# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize preselected native notes and field headings without new inference."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceBlock
    from computer_use.interaction.read.presentation.interface.text.formatting import InterfaceWriter


def source_slot_label(writer: InterfaceWriter, ref: str, key: str, label: str | None) -> str | None:
    """Name the original description source and its selected visual-row neighbours."""
    plan = writer.request.readable
    style = plan.nodes.get(ref) if plan is not None else None
    if key != "description" or style is None or not style.visual_row:
        return label
    return (label or "Description") + " [element " + ref + "; same visual row " + reference_set(style.visual_row) + "]"


def note_lines(writer: InterfaceWriter, ref: str, *, include_owner: bool = True) -> tuple[str, ...]:
    """Keep exact original slot labels, values and action names under their owner."""
    plan = writer.request.readable
    style = plan.nodes.get(ref) if plan is not None else None
    if style is None:
        return ()
    atom = writer.atoms[ref]
    fields = [
        (slot.label or slot.key.capitalize()) + ": " + writer.value(ref, slot)
        for slot in atom.slots
        if slot.key in style.note_slots
    ]
    # An existing shared action declaration already carries every source owner.
    omitted = writer.request.sharing.omitted_actions.get(ref, ()) if writer.request.sharing else ()
    from computer_use.interaction.read.presentation.interface.text.formatting import field
    from computer_use.interaction.read.presentation.rendering.formatting import quoted

    actions = [
        field(action.label)
        + (" [name: " + quoted(action.name) + "]" if action.ambiguous or action.label != action.name else "")
        for action in atom.actions
        if action.catalogue_index in style.note_actions and action.catalogue_index not in omitted
    ]
    if actions:
        fields.append("Secondary Actions: " + ", ".join(actions))
    prefix = ref + " " + field(atom.role.label) + ": " if include_owner else ""
    return (prefix + ", ".join(fields),) if fields else ()


def notes_only(writer: InterfaceWriter, ref: str) -> bool:
    """Return whether this complete passive source is retained by its numbered note."""
    plan = writer.request.readable
    style = plan.nodes.get(ref) if plan is not None else None
    return style is not None and style.notes_only


def packed_atom(lines: tuple[str, ...], ref: str, depth: int, *, readable: bool) -> tuple[str, tuple[str, ...]]:
    """Separate a packed control's explanations while naming their exact owner."""
    if not readable:
        return " ".join(lines), ()
    if len(lines) == 1:
        return lines[0], ()
    indent = "\t" * (depth + 1)
    return lines[0], (indent + ref + ":", *(indent + line for line in lines[1:]))


def write_heading(
    writer: InterfaceWriter, block: InterfaceBlock, depth: int, context: tuple[tuple[str, str], ...]
) -> str | None:
    """Write the exact selected question, a neutral field, or a transparent tier.

    Returns:
        The displayed heading, an empty string for a transparent tier, or None
        when the ordinary field-heading serializer owns this block.
    """
    plan = writer.request.readable
    heading = plan.headings.get(block.key) if plan is not None else None
    if heading is None:
        return None
    writer.remember(heading.ref, depth, context)
    if heading.transparent:
        return ""
    if heading.caption is not None:
        text = writer.value(heading.sources[0], heading.caption)
        line = "\t" * depth + text + " [elements " + reference_set(heading.sources) + "]:"
    else:
        line = "\t" * depth + heading.ref + " Field:"
    writer.lines.append(line)
    return line
