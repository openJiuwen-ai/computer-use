# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source-bound atomic presentation fields prepared before text serialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.ir import FactKey, PresentedValue

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionInput, ProjectionResult
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument


SlotFormat = Literal["text", "literal", "number", "span", "selection", "refs", "toggle", "unavailable", "preview"]


@dataclass(frozen=True)
class AtomFact:
    """An exact captured field occurrence, including its original value and type."""

    key: FactKey
    value: object


@dataclass(frozen=True)
class AtomRole:
    """Role spelling and the source fields supporting that spelling."""

    label: str
    facts: tuple[AtomFact, ...] = ()
    source_description: bool = False


@dataclass(frozen=True)
class AtomState:
    """One independently meaningful state or operation capability."""

    label: str
    facts: tuple[AtomFact, ...] = ()


@dataclass(frozen=True)
class AtomPreview:
    """Exact compact carrier or incidental URL display with its full value kept separately.

    The slot value retains the original capture. This record specifies only what
    the terminal formatter emits; source accounting routes the full field to details.
    """

    kind: Literal["embedded", "embedded-url", "url"]
    head: str
    encoded_characters: int | None = None


@dataclass(frozen=True)
class AtomSlot:
    """One ordered field slot containing values, rather than a generated node line.

    Attributes:
        key: Local field identity used by layout and source-disposition records.
        label: Human-facing field label; None means an unlabelled caption.
        value: Original or explicitly derived scalar/record to serialize.
        format: Terminal formatting rule for this value's type and meaning.
        placement: Caption, ordinary inline field, or independent text block.
        facts: Original field occurrences represented by this slot.
        provenance: Compression's exact composition record, when available.
        protection: Meaning requiring inline disclosure, such as input or selection.
        preview: Preselected carrier/URL summary with exact full value retained above.
    """

    key: str
    label: str | None
    value: object
    format: SlotFormat = "text"
    placement: Literal["caption", "inline", "block"] = "inline"
    facts: tuple[AtomFact, ...] = ()
    provenance: PresentedValue | None = None
    protection: str | None = None
    preview: AtomPreview | None = None


@dataclass(frozen=True)
class AtomAction:
    """One native action with its executable owner retained after display folding."""

    source_ref: str
    name: str
    label: str
    ambiguous: bool
    catalogue_index: int
    source: Mapping[str, object]


@dataclass(frozen=True)
class AtomCoverage:
    """Collected child coverage and precise continuation evidence for an atom."""

    reason: str | None = None
    read: int | None = None
    total: int | None = None
    start: int | None = None
    end: int | None = None
    next_offset: int | None = None
    omissions: tuple[str, ...] = ()
    unexpanded_menu: bool = False

    @property
    def partial(self) -> bool:
        return (
            self.reason is not None
            or self.next_offset is not None
            or bool(self.omissions)
            or self.read is not None
            and self.total is not None
            and self.read < self.total
            and self.end != self.total
        )


@dataclass(frozen=True)
class AtomDeclaration:
    """A shared source-field value with an explicit, ordered set of real owners."""

    attribute: str
    value: object
    facts: tuple[AtomFact, ...]


@dataclass(frozen=True)
class AtomDisposition:
    """An exact field occurrence's destination within this captured presentation."""

    fact: AtomFact
    target_ref: str
    slot: str
    kind: Literal["inline", "equivalent", "details"]


@dataclass(frozen=True)
class AtomFieldClaim:
    """A normalized field value actually represented by an atomic display destination.

    Values use the same normalized-field contract as the existing native-field
    accounting. They remain separate from exact native ``AtomFact`` values.
    """

    ref: str
    field: str
    value: object
    target: str
    kind: Literal["display", "equivalent", "shared", "continuation", "text-selection-summary"] = "display"


@dataclass(frozen=True)
class AtomPlan:
    """All atomic display decisions for a real element, independent of block layout.

    The original fields remain frozen alongside the selected slots. Layout may
    share or compose slots while their exact source occurrences stay identifiable.
    Actions retain the original source even when their caption was folded here.
    """

    ref: str
    parent: str | None
    role: AtomRole
    states: tuple[AtomState, ...]
    slots: tuple[AtomSlot, ...]
    actions: tuple[AtomAction, ...]
    actions_unavailable: bool
    coverage: AtomCoverage
    children: tuple[str, ...]
    source_fields: Mapping[str, object]
    dispositions: tuple[AtomDisposition, ...]
    declarations: tuple[AtomDeclaration, ...] = ()
    field_claims: tuple[AtomFieldClaim, ...] = ()

    def slot(self, key: str) -> AtomSlot | None:
        """Return the slot representing a particular local semantic field."""
        return next((item for item in self.slots if item.key == key), None)


@dataclass(frozen=True)
class AtomDocument:
    """Frozen per-element content used by block planning and terminal rendering."""

    source: ProjectionInput
    atoms: Mapping[str, AtomPlan]
    roots: tuple[str, ...]
    order: tuple[str, ...]
    focused_ref: str | None
    selected_refs: tuple[str, ...]
    dispositions: tuple[AtomDisposition, ...]
    field_claims: tuple[AtomFieldClaim, ...] = ()
    deferred_native_fields: tuple[Mapping[str, object], ...] = ()
    deferred_menu_actions: tuple[Mapping[str, object], ...] = ()
    deferred_control_raises: tuple[Mapping[str, object], ...] = ()
    deferred_native_urls: tuple[Mapping[str, object], ...] = ()
    deferred_native_url_peers: tuple[Mapping[str, object], ...] = ()
    shared_index_basis: bool = False


@dataclass(frozen=True)
class AtomRequest:
    """Final projection and captured formatting policy for atomic field planning."""

    projection: ProjectionResult
    branch: BranchFacts


@dataclass(frozen=True)
class AtomFinalizeRequest:
    """Prepared content, layout context and captured detail routes for final display choices."""

    atoms: AtomDocument
    projection: ProjectionResult
    branch: BranchFacts
    disclosure: DisclosurePlan | None = None
    interface: InterfaceDocument | None = None
