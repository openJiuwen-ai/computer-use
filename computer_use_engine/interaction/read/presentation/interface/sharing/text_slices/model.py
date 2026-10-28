# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable sources and selection results for complete ancestor text reuse."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument, AtomSlot
from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan, TextFragment
from computer_use.interaction.read.presentation.rendering.layout.text_slices import TextSliceSources
from computer_use.protocol.observation import NativeObservation


@dataclass(frozen=True)
class TextSliceRequest:
    """Final public slots and exact source bindings available for full-text reuse."""

    atoms: AtomDocument
    native: NativeObservation
    refs: Mapping[str, str]
    value_fields: Mapping[str, str] | None = None
    disclosure: DisclosurePlan | None = None
    excluded_sources: frozenset[str] = frozenset()
    excluded_slots: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class TextSliceField:
    """An independently numbered scalar with one complete original native field."""

    ref: str
    slot: str
    native_token: str
    native_path: tuple[str, ...]
    scope: tuple[str, str, str]
    value: str
    atom_slot: AtomSlot


@dataclass(frozen=True)
class TextSliceEvidence:
    """Complete fields and original parent-chain evidence, without rendered text."""

    request: TextSliceRequest
    native: TextSliceSources
    fields: Mapping[tuple[str, str], TextSliceField]


@dataclass(frozen=True)
class TextSlicePlan:
    """Readable reuse of an exact range in a complete public ancestor.

    The source and anchor retain their complete AtomSlot values and provenance.
    Only the full-view scalar spelling changes; native bindings, actions, states
    and individual diff records remain owned by their original elements. UTF-16
    spans prove source equality internally. Excerpt fragments use Python string
    offsets into the source, and are selected before final text serialization.
    """

    source: TextSliceField
    anchor: TextSliceField
    start_utf16: int
    end_utf16: int
    estimated_saved_bytes: int
    mode: Literal["same", "excerpt"] = "excerpt"
    fragments: tuple[TextFragment, ...] = ()


@dataclass(frozen=True)
class TextSliceDocument:
    """Frozen full-view scalar choices and the anchors that must remain literal."""

    plans: Mapping[tuple[str, str], TextSlicePlan] = field(default_factory=lambda: MappingProxyType({}))
    anchors: frozenset[tuple[str, str]] = frozenset()

    def plan_by(self, ref: str, slot: str) -> TextSlicePlan | None:
        """Return a complete source-specific substitution selected before serialization."""
        return self.plans.get((ref, slot))


@dataclass(frozen=True)
class SelectedTextSlices:
    """Chosen immutable substitutions awaiting source and range verification."""

    evidence: TextSliceEvidence
    document: TextSliceDocument
