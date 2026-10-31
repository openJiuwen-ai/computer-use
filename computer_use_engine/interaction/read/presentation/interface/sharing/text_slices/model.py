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
    """Final public slots and exact source bindings available for full-text reuse.

    Attributes:
        atoms: Source-bound atomic fields, states, actions, and coverage.
        native: Immutable normalized native observation.
        refs: Published references mapped to original native object tokens.
        value_fields: Original per-reference primary value selectors used to establish field identity.
        disclosure: Previously selected field excerpts and their recovery destinations.
        excluded_sources: Original references that must retain their independent scalar presentation.
        excluded_slots: Original reference/slot pairs excluded from text reuse.
    """

    atoms: AtomDocument
    native: NativeObservation
    refs: Mapping[str, str]
    value_fields: Mapping[str, str] | None = None
    disclosure: DisclosurePlan | None = None
    excluded_sources: frozenset[str] = frozenset()
    excluded_slots: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class TextSliceField:
    """An independently numbered scalar with one complete original native field.

    Attributes:
        ref: Original element reference within this captured observation.
        slot: Local scalar-slot key on the original atom.
        native_token: Driver-owned object identity associated with the original reference.
        native_path: Exact normalized field path on that original native object.
        scope: Original session, observation, and native window/root identities.
        value: Complete captured value with its original scalar type.
        atom_slot: Complete immutable scalar slot and its source provenance.
    """

    ref: str
    slot: str
    native_token: str
    native_path: tuple[str, ...]
    scope: tuple[str, str, str]
    value: str
    atom_slot: AtomSlot


@dataclass(frozen=True)
class TextSliceEvidence:
    """Complete fields and original parent-chain evidence, without rendered text.

    Attributes:
        request: Immutable inputs and policy for this local planning stage.
        native: Original parent-chain and source-range evidence for text reuse.
        fields: Complete candidate scalar fields keyed by original reference and slot.
    """

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

    Attributes:
        source: Independent source field represented by the selected reuse rule.
        anchor: Complete ancestor field retaining the literal reference text.
        start_utf16: Inclusive UTF-16 code-unit offset within the anchor field.
        end_utf16: Exclusive UTF-16 code-unit offset within the anchor field.
        estimated_saved_bytes: Estimated encoded-output bytes saved by this substitution.
        mode: Exact whole-value reuse or a readable original-source excerpt.
        fragments: Retained exact source spans in source order.
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
    """Frozen full-view scalar choices and the anchors that must remain literal.

    Attributes:
        plans: Selected source substitutions keyed by original reference and slot.
        anchors: Reference/slot pairs that must retain their complete literal anchor values.
    """

    plans: Mapping[tuple[str, str], TextSlicePlan] = field(default_factory=lambda: MappingProxyType({}))
    anchors: frozenset[tuple[str, str]] = frozenset()

    def plan_by(self, ref: str, slot: str) -> TextSlicePlan | None:
        """Return a complete source-specific substitution selected before serialization."""
        return self.plans.get((ref, slot))


@dataclass(frozen=True)
class SelectedTextSlices:
    """Chosen immutable substitutions awaiting source and range verification.

    Attributes:
        evidence: Source-backed facts used to constrain presentation choices.
        document: Frozen selected text-reuse decisions.
    """

    evidence: TextSliceEvidence
    document: TextSliceDocument
