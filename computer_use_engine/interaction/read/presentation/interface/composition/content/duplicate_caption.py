# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Represent an equal child caption once while retaining its real element reference."""

from __future__ import annotations

from collections.abc import Mapping

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument, AtomPlan, AtomSlot
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDocument


def duplicate_caption_slots(
    member: AtomPlan,
    target: AtomPlan,
    block: InterfaceBlock,
    document: InterfaceDocument,
    atoms: AtomDocument,
) -> tuple[AtomSlot, ...]:
    """Select an exact, complete caption copy on a retained native child.

    Args:
        member: Original caption source with its complete fields and capabilities.
        target: Actual control whose native caption already has the same value.
        block: Proved one-control visual unit selected by geometric planning.
        document: Immutable source hierarchy and selected layout.
        atoms: Complete per-reference content plans and text completeness evidence.

    Returns:
        Original slots eligible for an explicit caption-reference representation.
        Unknown SDK capabilities stay unknown on the retained child; known input,
        actions, states, extra fields and incomplete observations keep the full atom.
    """
    source = member.source_fields
    caps = source.get("capabilities")
    if (
        not block.visual_unit
        or block.layout != "control-unit"
        or target.source_fields.get("role") not in ("button", "link")
        or source.get("role") != "staticText"
        or member.children
        or member.states
        or member.actions
        or member.actions_unavailable
        or member.declarations
        or member.coverage.partial
        or atoms.focused_ref == member.ref
        or not isinstance(caps, Mapping)
        or any(caps.get(key) not in ("unknown", "unavailable") for key in ("click", "typeText", "replaceText", "focus"))
        or "actions" not in source
        or source["actions"]
        or source.get("enabled") is False
        or any(
            source.get(key)
            for key in (
                "selected",
                "focused",
                "busy",
                "invalid",
                "editable",
                "pressable",
                "focusable",
                "selectable",
                "expandable",
                "submenu",
                "sensitive",
                "protected",
                "modal",
                "incompleteLabelRelation",
                "actionsUnavailable",
                "preserveGroup",
            )
        )
        or document.source.parents.get(member.ref) != target.ref
        or document.projected_parents.get(member.ref) != target.ref
        or tuple(document.projected_nodes[target.ref].get("children", ())) != (member.ref,)
    ):
        return ()
    captions = tuple(slot for slot in target.slots if slot.placement == "caption")
    slots = member.slots
    if (
        len(captions) != 1
        or captions[0].format != "text"
        or captions[0].preview is not None
        or not isinstance(captions[0].value, str)
        or not captions[0].value
        or not slots
        or any(
            slot.key not in ("name", "description", "value")
            or slot.protection
            or slot.format != "text"
            or slot.preview is not None
            or slot.value != captions[0].value
            or any(character in slot.value for character in ("\r", "\n", "\t"))
            for slot in slots
        )
    ):
        return ()
    evidence = atoms.source.context.get("compressionEvidence")
    if any(
        slot.key not in owner.source_fields.get("_completeTextValues", ())
        and not (
            isinstance(evidence, CompressionEvidence)
            and slot.facts
            and all((fact.key.source, fact.key.attribute) in evidence.complete_text for fact in slot.facts)
        )
        for owner, selected in ((member, slots), (target, captions))
        for slot in selected
    ):
        return ()
    return slots
