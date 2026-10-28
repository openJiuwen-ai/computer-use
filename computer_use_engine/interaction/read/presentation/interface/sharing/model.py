# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed, source-specific declarations shared within one captured native ancestor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from computer_use.interaction.read.presentation.actions.catalogue import ActionCatalogue
from computer_use.interaction.read.presentation.interface.atoms.model import AtomAction, AtomDocument, AtomSlot
from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument


@dataclass(frozen=True)
class SharedActionGroup:
    """One equal native secondary-action sequence with every original owner explicit.

    Attributes:
        refs: Original source references in their retained order.
        actions: Native action declarations retaining their executable owners.
        estimated_saved_characters: Estimated reduction in Python string characters.
    """

    refs: tuple[str, ...]
    actions: tuple[AtomAction, ...]
    estimated_saved_characters: int


@dataclass(frozen=True)
class SharedFieldGroup:
    """One exact field value declared for independent, completely captured source slots.

    Attributes:
        refs: Original source references in their retained order.
        slot_key: Stable local scalar-slot key on the original atom.
        slot: Complete source-bound atom slot retained for this field.
        source_slots: Each original reference paired with its complete independent field slot.
        estimated_saved_characters: Estimated reduction in Python string characters.
    """

    refs: tuple[str, ...]
    slot_key: str
    slot: AtomSlot
    source_slots: tuple[tuple[str, AtomSlot], ...]
    estimated_saved_characters: int


@dataclass(frozen=True)
class BlockSharing:
    """Declarations placed in a common display group within a trusted native ancestor.

    Attributes:
        actions: Native action declarations retaining their executable owners.
        fields: Source-bound fields available to this stage.
    """

    actions: tuple[SharedActionGroup, ...] = ()
    fields: tuple[SharedFieldGroup, ...] = ()


@dataclass(frozen=True)
class SharingEvidence:
    """Captured evidence used to select exact field and action declarations.

    ``protected_values`` retains native focus, selection and input evidence even
    when it cannot be expressed as a normalized selection range or capability.

    Attributes:
        actions: Complete action catalogues indexed by original source reference.
        complete_fields: Reference/field pairs whose complete original values were collected.
        contested_sources: Sources with conflicting ownership or field evidence.
        protected_values: References carrying native focus, selection, or input evidence requiring retention.
    """

    actions: Mapping[str, ActionCatalogue] = field(default_factory=lambda: MappingProxyType({}))
    complete_fields: frozenset[tuple[str, str]] = frozenset()
    contested_sources: frozenset[str] = frozenset()
    protected_values: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SharingRequest:
    """Final structured blocks, atomic content, and independent capture evidence.

    Attributes:
        interface: Structured block forest selected for this captured view.
        atoms: Source-bound atomic fields, states, actions, and coverage.
        evidence: Source-backed facts used to constrain presentation choices.
        disclosure: Previously selected field excerpts and their recovery destinations.
        excluded_sources: Original references that must retain their independent scalar presentation.
    """

    interface: InterfaceDocument
    atoms: AtomDocument
    evidence: SharingEvidence = field(default_factory=SharingEvidence)
    disclosure: DisclosurePlan | None = None
    excluded_sources: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SharingPlan:
    """Frozen declarations and exact member slots suppressed only in full block text.

    Atomic nodes stay unchanged for individual reads and diff. Action exclusions
    name original catalogue ordinals on each original source. A declaration does
    not establish a shared executable target.

    Attributes:
        by_block: Selected declarations keyed by internal block identity.
        omitted_slots: Original reference-to-slot keys represented by a shared or recovered destination.
        omitted_actions: Per-source native catalogue indices represented by shared declarations.
    """

    by_block: Mapping[str, BlockSharing] = field(default_factory=lambda: MappingProxyType({}))
    omitted_slots: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))
    omitted_actions: Mapping[str, frozenset[int]] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class SharingDraft:
    """Local plan under construction; immutable input facts are retained in its request.

    Attributes:
        request: Immutable inputs and policy for this local planning stage.
        plan: Local immutable presentation decisions accumulated by this stage.
    """

    request: SharingRequest
    plan: SharingPlan = field(default_factory=SharingPlan)
