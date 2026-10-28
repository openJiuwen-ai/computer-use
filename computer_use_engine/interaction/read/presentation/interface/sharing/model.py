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
    """One equal native secondary-action sequence with every original owner explicit."""

    refs: tuple[str, ...]
    actions: tuple[AtomAction, ...]
    estimated_saved_characters: int


@dataclass(frozen=True)
class SharedFieldGroup:
    """One exact field value declared for independent, completely captured source slots."""

    refs: tuple[str, ...]
    slot_key: str
    slot: AtomSlot
    source_slots: tuple[tuple[str, AtomSlot], ...]
    estimated_saved_characters: int


@dataclass(frozen=True)
class BlockSharing:
    """Declarations placed in a common display group within a trusted native ancestor."""

    actions: tuple[SharedActionGroup, ...] = ()
    fields: tuple[SharedFieldGroup, ...] = ()


@dataclass(frozen=True)
class SharingEvidence:
    """Captured evidence used to select exact field and action declarations.

    ``protected_values`` retains native focus, selection and input evidence even
    when it cannot be expressed as a normalized selection range or capability.
    """

    actions: Mapping[str, ActionCatalogue] = field(default_factory=lambda: MappingProxyType({}))
    complete_fields: frozenset[tuple[str, str]] = frozenset()
    contested_sources: frozenset[str] = frozenset()
    protected_values: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SharingRequest:
    """Final structured blocks, atomic content, and independent capture evidence."""

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
    """

    by_block: Mapping[str, BlockSharing] = field(default_factory=lambda: MappingProxyType({}))
    omitted_slots: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))
    omitted_actions: Mapping[str, frozenset[int]] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class SharingDraft:
    """Local plan under construction; immutable input facts are retained in its request."""

    request: SharingRequest
    plan: SharingPlan = field(default_factory=SharingPlan)
