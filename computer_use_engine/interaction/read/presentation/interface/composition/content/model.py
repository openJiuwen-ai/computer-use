# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source dispositions for presenting complete form fields at one reading position."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from computer_use.interaction.read.presentation.interface.atoms.model import (
    AtomDisposition,
    AtomDocument,
    AtomFieldClaim,
    AtomSlot,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument


@dataclass(frozen=True)
class FieldContribution:
    """One exact member slot and its selected destination in the input's content.

    Attributes:
        source_ref: Real original owner of the contributed label or description.
        source_slot: Complete original slot, retaining value, facts and provenance.
        target_slot: Key of the input slot that expresses this contribution.
    """

    source_ref: str
    source_slot: AtomSlot
    target_slot: str


@dataclass(frozen=True)
class FieldMemberPresentation:
    """How one field member remains discoverable beside its original input.

    Attributes:
        ref: Original independently addressable source reference.
        mode: Complete node, original-source action line, composed passive text,
            or an explicit original reference for an equal child caption.
        consumed_slots: Exact source slots represented in the target field.
    """

    ref: str
    mode: Literal["complete", "action-source", "composed", "caption-reference"]
    consumed_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldPresentation:
    """Prepared input line, associated help and per-source disclosure decisions.

    The terminal writer consumes these selected slots directly. It does not infer
    ownership or remove repeated strings while formatting. Independent actions
    continue to use the member's original reference.

    ``heading_ref`` selects an uncomposed original label to precede the controls.
    It remains independently addressable and keeps every field and action. A
    missing heading means the caption has already been composed into target slots.
    """

    block_key: str
    target_ref: str
    target_slots: tuple[AtomSlot, ...]
    members: tuple[FieldMemberPresentation, ...]
    contributions: tuple[FieldContribution, ...]
    dispositions: tuple[AtomDisposition, ...]
    field_claims: tuple[AtomFieldClaim, ...]
    heading_ref: str | None = None


@dataclass(frozen=True)
class FieldContentPlan:
    """Frozen selected form presentations indexed by their synthetic block identity."""

    fields: Mapping[str, FieldPresentation]


@dataclass(frozen=True)
class FieldContentRequest:
    """Source-organized blocks and atoms with an explicit captured-details promise."""

    interface: InterfaceDocument
    atoms: AtomDocument
    captured_details: bool = False
