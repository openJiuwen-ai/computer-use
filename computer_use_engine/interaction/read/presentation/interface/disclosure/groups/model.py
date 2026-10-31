# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured cross-object overviews and their exact, observation-local recovery scope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument, AtomSlot
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import CapturedSnapshot
    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan


@dataclass(frozen=True)
class GroupDisclosurePolicy:
    """Minimum benefit and recovery allowance for optional captured-content overviews.

    Recovery weight is a conservative local cost allowance, not a measured task
    probability. The raw recovery cost is retained separately for acceptance.

    Attributes:
        minimum_objects: Minimum number of original objects required for an overview.
        minimum_characters: Minimum captured Python-character count before overview assessment.
        head_characters: Python characters retained at the beginning of an overview.
        tail_characters: Python characters retained at the end of an overview.
        minimum_saved_units: Minimum estimated token-cost saving after recovery allowance.
        minimum_saved_fraction: Minimum saving as a fraction of the original display estimate.
        recovery_weight: Local weighting applied to estimated recovery cost.
        recovery_page_characters: Python characters assumed per detail-recovery page.
        recovery_call_units: Estimated token-cost allowance for each recovery call.
    """

    minimum_objects: int = 3
    minimum_characters: int = 2048
    head_characters: int = 240
    tail_characters: int = 120
    minimum_saved_units: int = 256
    minimum_saved_fraction: float = 0.2
    recovery_weight: float = 0.1
    recovery_page_characters: int = 4096
    recovery_call_units: int = 32

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.minimum_objects,
                self.minimum_characters,
                self.head_characters,
                self.tail_characters,
                self.minimum_saved_units,
                self.recovery_call_units,
            )
        ):
            raise ValueError("Group disclosure counts must be nonnegative integers")
        if self.minimum_objects < 2 or self.head_characters + self.tail_characters == 0:
            raise ValueError("Group disclosure requires several sources and an original excerpt")
        if (
            not 0 < self.minimum_saved_fraction <= 1
            or not 0 <= self.recovery_weight <= 1
            or type(self.recovery_page_characters) is not int
            or self.recovery_page_characters <= 0
        ):
            raise ValueError("Invalid group recovery or saving policy")


@dataclass(frozen=True)
class WindowDisclosureContext:
    """Explicit directory evidence for assessing a blocked background window.

    The App read composer owns these facts. Per-tree parentage or screen geometry
    cannot establish them. A current modal window always remains expanded.
    ``scope_confirmed`` checks the collected root against its fixed native binding;
    ``directory_complete`` separately records whether other windows may be missing.

    Attributes:
        window_ref: Published window-directory reference for this capture.
        current: Whether this is the currently observed member window.
        modal: Reported native modal state, or None when unknown.
        confirmed_blockers: Confirmed modal descendant-window references blocking this window.
        directory_complete: Whether the source window directory was completely collected.
        scope_confirmed: Whether the collected root matches its original fixed window binding.
    """

    window_ref: int
    current: bool
    modal: bool | None
    confirmed_blockers: tuple[int, ...] = ()
    directory_complete: bool = False
    scope_confirmed: bool = False


@dataclass(frozen=True)
class GroupDisclosureRequest:
    """Frozen structured content and capture facts for one root's overview selection.

    Attributes:
        interface: Structured block forest selected for this captured view.
        atoms: Source-bound atomic fields, states, actions, and coverage.
        snapshot: Frozen capture and native binding metadata used to verify recovery routes.
        enabled: Whether this request enables the optional presentation transformation.
        policy: Thresholds and recovery allowances used to choose candidates.
        window_context: Independent directory and modal-chain evidence for this member.
        field_disclosure: Already selected field excerpts retained during group planning.
        recovery_prefix_units: Estimated recovery units preceding this root in the detail document.
    """

    interface: InterfaceDocument
    atoms: AtomDocument
    snapshot: CapturedSnapshot
    enabled: bool = False
    policy: GroupDisclosurePolicy = field(default_factory=GroupDisclosurePolicy)
    window_context: WindowDisclosureContext | None = None
    field_disclosure: DisclosurePlan | None = None
    recovery_prefix_units: int = 0

    def __post_init__(self) -> None:
        if type(self.recovery_prefix_units) is not int or self.recovery_prefix_units < 0:
            raise ValueError("Captured content recovery prefix must be a nonnegative integer")


@dataclass(frozen=True)
class CapturedGroupField:
    """A native field occurrence before normalization, including its original scalar type.

    Attributes:
        ref: Original element reference within this captured observation.
        native_field: Original provider selector retained in captured details.
        value: Complete captured value with its original scalar type.
        normalized: Normalized scalar corresponding to the original provider value.
        attribute: Projected scalar-slot name associated with the provider field.
    """

    ref: str
    native_field: str
    value: object
    normalized: object
    attribute: str = ""


@dataclass(frozen=True)
class GroupField:
    """One hidden presentation field with its full value and exact original fact provenance.

    Attributes:
        ref: Original element reference within this captured observation.
        slot_key: Stable local scalar-slot key on the original atom.
        slot: Complete source-bound atom slot retained for this field.
        sources: Complete original native occurrences supporting this presentation field.
    """

    ref: str
    slot_key: str
    slot: AtomSlot
    sources: tuple[CapturedGroupField, ...] = ()


@dataclass(frozen=True)
class GroupFragment:
    """Original characters from a source field; offsets are Python-character half-open ranges.

    Attributes:
        ref: Original element reference within this captured observation.
        slot_key: Stable local scalar-slot key on the original atom.
        start: Inclusive Python Unicode-code-point offset in the original string.
        end: Exclusive Python Unicode-code-point offset in the original string.
        text: Exact captured substring for this span.
        source: Exact provider occurrence supporting this substring, when available.
    """

    ref: str
    slot_key: str
    start: int
    end: int
    text: str
    source: CapturedGroupField | None = None


@dataclass(frozen=True)
class GroupEntry:
    """One catalogue object with unchanged identity, hierarchy, role and full source-bound fields.

    Attributes:
        ref: Original element reference within this captured observation.
        parent: Original projected parent reference, when present.
        role: Source-backed role selected for this reading unit.
        fields: Complete original field slots retained in this catalogue entry.
    """

    ref: str
    parent: str | None
    role: str
    fields: tuple[GroupField, ...]


@dataclass(frozen=True)
class GroupOverview:
    """A contiguous captured region replaced by an explicit, reversible overview.

    Attributes:
        child_keys: Exact consecutive child blocks replaced under parent_key.
        projected_refs: Real atoms still retained independently for actions and diff.
        recovery_refs: Those sources and every folded source represented by them.
        entries: Complete structured field catalogue, rendered before technical details.
        outline: Full captured headings and named region labels, in their source order.
        captured_records: Number of actual sibling record units, never an inferred total.
        recovery_units: Estimated cost through this catalogue, including earlier groups.
    """

    section_key: str
    capture: tuple[str, str, str]
    parent_key: str
    child_keys: tuple[str, ...]
    projected_refs: tuple[str, ...]
    recovery_refs: tuple[str, ...]
    entries: tuple[GroupEntry, ...]
    fields: tuple[GroupField, ...]
    outline: tuple[GroupField, ...]
    head: tuple[GroupFragment, ...]
    tail: tuple[GroupFragment, ...]
    kind: Literal["content", "records", "background"]
    captured_characters: int
    captured_records: int | None
    full_units: int
    overview_units: int
    recovery_units: int
    estimated_saved_units: int


@dataclass(frozen=True)
class BackgroundOverview(GroupOverview):
    """A confirmed blocked window's captured children with explicit residual state.

    The fixed window binding and known modal-parent chain establish blocking.
    Global directory completeness is independent: missing windows and collection
    gaps remain visible. Every child keeps its original atom and detail target.

    Attributes:
        window_ref: Published window-directory reference for this capture.
        blockers: Confirmed modal descendant windows explaining this overview.
        directory_complete: Whether the source window directory was completely collected.
        important_refs: Original references with state that must remain visible in the overview.
        uncertain_refs: Original references whose state or collection remains uncertain.
        nested_windows: Original nested-window references retained within this captured subtree.
        state_facts: Exact original reference, state-field name, and captured-value triples.
    """

    window_ref: int
    blockers: tuple[int, ...]
    directory_complete: bool
    important_refs: tuple[str, ...]
    uncertain_refs: tuple[str, ...]
    nested_windows: tuple[str, ...]
    state_facts: tuple[tuple[str, str, object], ...]


@dataclass(frozen=True)
class GroupAssessment:
    """A deterministic acceptance or rejection with its local source scope.

    Attributes:
        parent_key: Internal parent block identity for this candidate run.
        child_keys: Ordered consecutive child block identities in the assessed run.
        reason: Source-specific explanation for the selected decision or coverage gap.
        accepted: Whether the candidate satisfies this stage's evidence and benefit requirements.
    """

    parent_key: str
    child_keys: tuple[str, ...]
    reason: str
    accepted: bool = False


@dataclass(frozen=True)
class ClassifiedGroups:
    """Protected native sources and subtree membership before candidate selection.

    Attributes:
        request: Immutable inputs and policy for this local planning stage.
        protections: Original source references mapped to reasons requiring expanded disclosure.
        block_refs: Each internal block mapped to its complete original-source membership.
        assessments: Recorded acceptance and rejection decisions with their local scope.
        native_fields: Original reference/slot pairs mapped to their complete provider field occurrences.
    """

    request: GroupDisclosureRequest
    protections: Mapping[str, tuple[str, ...]]
    block_refs: Mapping[str, tuple[str, ...]]
    assessments: tuple[GroupAssessment, ...] = ()
    native_fields: Mapping[tuple[str, str], tuple[CapturedGroupField, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class SelectedGroups:
    """Nonoverlapping passive overviews awaiting exact capture and range validation.

    Attributes:
        classified: Candidate membership and protection evidence retained from classification.
        groups: Selected nonoverlapping overviews with exact same-capture recovery routes.
        assessments: Recorded acceptance and rejection decisions with their local scope.
    """

    classified: ClassifiedGroups
    groups: tuple[GroupOverview, ...]
    assessments: tuple[GroupAssessment, ...]


@dataclass(frozen=True)
class GroupDisclosurePlan:
    """Immutable overview lookup and explicit field destinations for terminal consumers.

    Full text replaces child runs. Individual atomic text and native bindings stay
    unchanged. Whole-observation read_details, with its real pagination, restores
    these catalogues; an individual element detail is not a subtree read.

    Attributes:
        groups: Selected nonoverlapping overviews with exact same-capture recovery routes.
        omitted_slots: Original reference-to-slot keys represented by a shared or recovered destination.
        protected_refs: Original references and the evidence requiring their independent disclosure.
        assessments: Recorded acceptance and rejection decisions with their local scope.
    """

    groups: tuple[GroupOverview, ...] = ()
    omitted_slots: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))
    protected_refs: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    assessments: tuple[GroupAssessment, ...] = ()
