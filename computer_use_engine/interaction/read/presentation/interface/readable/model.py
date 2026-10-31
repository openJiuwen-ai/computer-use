# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source-bound typography decisions made before comparison and serialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument, AtomSlot
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
from computer_use.interaction.read.presentation.interface.sharing.model import SharingPlan


@dataclass(frozen=True)
class ReadableNode:
    """Select field placement without changing a source value or capability.

    Attributes:
        note_slots: Exact slot keys printed in the same observation's native notes.
        block_slots: Fields displayed beneath the control rather than in its row.
        note_actions: Original catalogue ordinals moved to native notes.
        toggle_slot: Exact checked-state slot represented by the conventional marker.
        glyph: ASCII checkbox or radio marker; unknown values retain their Value.
        notes_only: A complete passive artifact represented by its numbered note.
        visual_row: Other original members beside this image's description. This
            records display adjacency, independently of native label/help relations.
    """

    note_slots: tuple[str, ...] = ()
    block_slots: tuple[str, ...] = ()
    note_actions: tuple[int, ...] = ()
    toggle_slot: str | None = None
    glyph: str | None = None
    notes_only: bool = False
    visual_row: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReadableHeading:
    """A real label retained in notes and an optional exact shared question caption.

    Attributes:
        ref: Original field-label element, still represented by its numbered note.
        caption: Existing shared description used verbatim as the visible heading.
        sources: Independent option elements declaring that exact description.
        shared_index: Declaration ordinal consumed by the heading serializer.
        transparent: Omit one redundant label tier while retaining its source note.
    """

    ref: str
    caption: AtomSlot | None = None
    sources: tuple[str, ...] = ()
    shared_index: int | None = None
    transparent: bool = False


@dataclass(frozen=True)
class ReadablePlan:
    """Immutable node typography and field captions; all source atoms stay intact.

    Attributes:
        nodes: Source-reference keys and their local field-placement decisions.
        headings: Layout-block keys and the exact caption or neutral heading to use.
    """

    nodes: Mapping[str, ReadableNode] = field(default_factory=lambda: MappingProxyType({}))
    headings: Mapping[str, ReadableHeading] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ReadableRequest:
    """Validated geometry and content plans supplying the final display vocabulary.

    Attributes:
        interface: Final source-bound reading blocks, without changes to geometry.
        atoms: Original typed slots, states, actions and coverage for each target.
        sharing: Validated declarations with all original fields and owners retained.
    """

    interface: InterfaceDocument
    atoms: AtomDocument
    sharing: SharingPlan


@dataclass(frozen=True)
class ReadableDraft:
    """Request-local placement decisions awaiting source-preservation validation.

    Attributes:
        request: Read-only facts and layout supporting this local draft.
        plan: Immutable decisions replaced independently by each planning Pass.
    """

    request: ReadableRequest
    plan: ReadablePlan
