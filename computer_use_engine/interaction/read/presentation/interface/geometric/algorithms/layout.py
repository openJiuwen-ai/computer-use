# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose final field templates from typed atoms and local geometric evidence."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.geometric.algorithms.rows import member_box
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.geometric.units_support import inline_unit_refs


def _caption_label(draft: InterfaceDraft, ref: str) -> bool:
    """Accept a plain text heading whose complete displayed value fits one caption."""
    if draft.atoms is None:
        return False
    atom = draft.atoms.atoms.get(ref)
    if (
        atom is None
        or atom.source_fields.get("role") != "staticText"
        or atom.children
        or atom.states
        or atom.actions
        or atom.actions_unavailable
        or atom.declarations
        or atom.coverage.partial
        or draft.atoms.focused_ref == ref
        or len(atom.slots) != 1
    ):
        return False
    slot = atom.slots[0]
    return (
        slot.key in ("name", "value", "description")
        and slot.format == "text"
        and isinstance(slot.value, str)
        and bool(slot.value.strip())
        and not any(character in slot.value for character in ("\r", "\n", "\t"))
    )


def _first_members(block: InterfaceBlock, draft: InterfaceDraft) -> tuple[str, ...]:
    members = tuple(key for key in block.children if draft.blocks[key].primary_ref != block.label_ref)
    if not members:
        return ()
    first = draft.blocks[members[0]]
    if first.layout == "visual-row":
        return first.children
    return (members[0],) if first.primary_ref is not None and not first.children else ()


def _inline_field(block: InterfaceBlock, draft: InterfaceDraft) -> bool:
    label = draft.source.geometry.boxes.get(block.label_ref)
    members = _first_members(block, draft)
    if label is None or not members or draft.atoms is None:
        return False
    unit_refs = tuple(inline_unit_refs(key, draft.blocks) for key in members)
    if any(refs is None for refs in unit_refs):
        return False
    refs = tuple(ref for unit in unit_refs for ref in unit)
    if not same_region(draft, (block.label_ref, *refs)):
        return False
    label_atom = draft.atoms.atoms[block.label_ref]
    if label_atom.declarations or any(
        slot.placement == "block" or isinstance(slot.value, str) and ("\n" in slot.value or "\r" in slot.value)
        for slot in label_atom.slots
    ):
        return False
    boxes = [member_box(key, draft, draft.blocks) for key in members]
    if any(
        box is None
        or box.space != label.space
        or label.vertical_overlap(box) < 0.6
        or box.x < label.right - 0.2 * label.width
        for box in boxes
    ):
        return False
    # Slot placement is prepared before formatting. Block-valued text keeps its
    # vertical layout even when the native rectangle happens to fit one row.
    atoms = [draft.atoms.atoms[ref] for ref in refs]
    return all(
        not atom.declarations
        and all(
            slot.placement != "block"
            and (not isinstance(slot.value, str) or "\n" not in slot.value and "\r" not in slot.value)
            for slot in atom.slots
        )
        for atom in atoms
    )


def select_templates(draft: InterfaceDraft) -> InterfaceDraft:
    """Select heading and first-row placement without modifying source fields.

    Args:
        draft: Structured visual membership, source atoms and complete local bounds.

    Returns:
        The same source graph with explicit final field styles. Plain labels keep
        their number and text; labels with actions or additional facts keep their
        full atomic form. Additional field rows remain below the first line.
    """
    if not draft.enabled:
        return draft
    blocks, decisions = dict(draft.blocks), []
    for key, block in draft.blocks.items():
        if block.kind != "field" or block.primary_ref is not None or block.label_ref is None:
            continue
        style = "caption" if _caption_label(draft, block.label_ref) else "atom"
        layout = "field-inline" if block.field_target_ref is None and _inline_field(block, draft) else "fields"
        if block.visual_unit and block.field_target_ref is not None and draft.atoms is not None:
            refs = tuple(ref for ref, _ in block.member_roles)
            if all(
                not draft.atoms.atoms[ref].declarations
                and all(
                    slot.placement != "block"
                    and (not isinstance(slot.value, str) or "\n" not in slot.value and "\r" not in slot.value)
                    for slot in draft.atoms.atoms[ref].slots
                )
                for ref in refs
            ):
                layout = "control-unit"
        blocks[key] = replace(block, layout=layout, heading_style=style)
        decisions.append(f"geometric-field-template:{key}:{layout}:{style}")
    return revise(draft, blocks, *decisions)
