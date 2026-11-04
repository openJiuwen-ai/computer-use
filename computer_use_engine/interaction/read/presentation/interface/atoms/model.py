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
    """An exact captured field occurrence, including its original value and type.

    Attributes:
        key: Exact source node, field path, and occurrence identity.
        value: Complete captured value with its original scalar type.
    """

    key: FactKey
    value: object


@dataclass(frozen=True)
class AtomRole:
    """Role spelling and the source fields supporting that spelling.

    Attributes:
        label: Display spelling selected for this source-backed declaration.
        facts: Exact captured occurrences supporting this declaration.
        source_description: Whether the selected spelling comes from the provider role description.
    """

    label: str
    facts: tuple[AtomFact, ...] = ()
    source_description: bool = False


@dataclass(frozen=True)
class AtomState:
    """One independently meaningful state or operation capability.

    Attributes:
        label: Display spelling selected for this source-backed declaration.
        facts: Exact captured occurrences supporting this declaration.
    """

    label: str
    facts: tuple[AtomFact, ...] = ()


@dataclass(frozen=True)
class AtomPreview:
    """Exact compact carrier or incidental URL display with its full value kept separately.

    The slot value retains the original capture. This record specifies only what
    the terminal formatter emits; source accounting routes the full field to details.

    Attributes:
        kind: Selected embedded-carrier or URL preview format.
        head: Exact retained prefix for terminal display.
        encoded_characters: Original encoded-character count when the preview describes a carrier.
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
    """One native action with its executable owner retained after display folding.

    Attributes:
        source_ref: Original element owning this action or field occurrence.
        name: Exact native action name accepted by the execution interface.
        label: Display spelling selected for this source-backed declaration.
        ambiguous: Whether the display label also needs its exact native name to distinguish the action.
        catalogue_index: Zero-based position in the original owner action catalogue.
        source: Original action record used to check source-preserving delivery.
    """

    source_ref: str
    name: str
    label: str
    ambiguous: bool
    catalogue_index: int
    source: Mapping[str, object]


@dataclass(frozen=True)
class AtomCoverage:
    """Collected child coverage and precise continuation evidence for an atom.

    Attributes:
        reason: Source-specific explanation for the selected decision or coverage gap.
        read: Number of direct child facts collected, when known.
        total: Reported total direct-child count, when known.
        start: Inclusive collected child-index boundary, when known.
        end: Exclusive collected child-index boundary, when known.
        next_offset: Reported continuation offset requiring further collection.
        omissions: Retained explanations of omitted child facts.
        unexpanded_menu: Whether this coverage describes a deliberately unexpanded menu.
    """

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
    """A shared source-field value with an explicit, ordered set of real owners.

    Attributes:
        attribute: Projected field name used for source accounting.
        value: Complete captured value with its original scalar type.
        facts: Exact captured occurrences supporting this declaration.
    """

    attribute: str
    value: object
    facts: tuple[AtomFact, ...]


@dataclass(frozen=True)
class AtomDisposition:
    """An exact field occurrence's destination within this captured presentation.

    Attributes:
        fact: Original captured field occurrence whose destination is recorded.
        target_ref: Original element receiving this field composition.
        slot: Destination scalar-slot key.
        kind: Inline, equivalent, or same-observation-details coverage for this occurrence.
    """

    fact: AtomFact
    target_ref: str
    slot: str
    kind: Literal["inline", "equivalent", "details"]


@dataclass(frozen=True)
class AtomFieldClaim:
    """A normalized field value actually represented by an atomic display destination.

    Values use the same normalized-field contract as the existing native-field
    accounting. They remain separate from exact native ``AtomFact`` values.

    Attributes:
        ref: Original element reference within this captured observation.
        field: Exact normalized field path accounted for by this claim.
        value: Normalized value expressed at the selected destination.
        target: Named display or recovery destination for this field.
        kind: Coverage mechanism expressing this field, including selection summaries.
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

    Attributes:
        ref: Original element reference within this captured observation.
        parent: Original projected parent reference, when present.
        role: Source-backed role selected for this reading unit.
        states: Ordered state declarations with their original source evidence.
        slots: Ordered scalar fields retained for terminal formatting.
        actions: Native action declarations retaining their executable owners.
        actions_unavailable: Whether the native action catalogue could not be collected.
        coverage: Complete child coverage and continuation information.
        children: Ordered direct projected child references.
        source_fields: Original normalized fields kept independently of selected display slots.
        dispositions: Original fact occurrences mapped to their selected display destinations.
        declarations: Shared exact field values and their ordered source facts.
        field_claims: Exact normalized fields and the display destinations accounting for them.
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
    """Frozen per-element content used by block planning and terminal rendering.

    Attributes:
        source: Immutable projection input from which these atoms were prepared.
        atoms: Source-bound atomic fields, states, actions, and coverage.
        roots: Ordered roots of the selected block or atomic forest.
        order: Original projected references in captured traversal order.
        focused_ref: Original reference reporting focus in this projection, when known.
        selected_refs: Original references reporting selection in captured order.
        dispositions: Original fact occurrences mapped to their selected display destinations.
        field_claims: Exact normalized fields and the display destinations accounting for them.
        deferred_native_fields: Exact native fields accounted for by same-observation details.
        deferred_menu_actions: Menu mechanisms whose exact native actions remain in details.
        deferred_control_raises: Control-focus actions whose exact native names remain in details.
        deferred_native_urls: Source-specific native URL fields with verified detail destinations.
        deferred_native_url_peers: Peer-caption evidence supporting exact URL detail choices.
        shared_index_basis: Whether this document explicitly shares the captured index interpretation.
    """

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
    """Final projection and captured formatting policy for atomic field planning.

    Attributes:
        projection: Immutable compression result supplying this presentation scope.
        branch: Captured formatting policy and source scope for this branch.
    """

    projection: ProjectionResult
    branch: BranchFacts


@dataclass(frozen=True)
class AtomFinalizeRequest:
    """Prepared content, layout context and captured detail routes for final display choices.

    Attributes:
        atoms: Source-bound atomic fields, states, actions, and coverage.
        projection: Immutable compression result supplying this presentation scope.
        branch: Captured formatting policy and source scope for this branch.
        disclosure: Previously selected field excerpts and their recovery destinations.
        interface: Structured block forest selected for this captured view.
    """

    atoms: AtomDocument
    projection: ProjectionResult
    branch: BranchFacts
    disclosure: DisclosurePlan | None = None
    interface: InterfaceDocument | None = None
