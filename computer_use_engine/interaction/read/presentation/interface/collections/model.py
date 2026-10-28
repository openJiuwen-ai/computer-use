# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable logical collection layouts prepared without generating display text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import (
        AtomDocument,
        AtomRole,
        AtomSlot,
        AtomState,
    )
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.rendering.layout.model import RowCollection
    from computer_use.interaction.read.presentation.rendering.layout.table_containment import NativeTableGrid
    from computer_use.protocol.observation import NativeObservation


@dataclass(frozen=True)
class TableCellPlan:
    """A captured cell, its zero-based logical extent and original content subtree.

    Attributes:
        ref: Published identity of the actual cell.
        row: Reported zero-based row at which this cell starts.
        column: Reported zero-based column at which this cell starts.
        row_span: Reported number of logical rows occupied by this cell.
        column_span: Reported number of logical columns occupied by this cell.
        children: Direct projected child identities in their captured order.
        descendant_refs: Preorder identities under this cell, excluding the cell.
        action_refs: Original action owners, including folded action sources.
        partial: Whether any retained atom has an unresolved coverage gap.
        inline_chain: Complete short cell-to-passive-child chain eligible for inline
            hierarchy; each member keeps its own role, fields and reference.
    """

    ref: str
    row: int
    column: int
    row_span: int
    column_span: int
    children: tuple[str, ...]
    descendant_refs: tuple[str, ...]
    action_refs: tuple[str, ...]
    partial: bool = False
    inline_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableRowPlan:
    """One original row with its independent index, outline level and cells.

    Attributes:
        ref: Original element reference within this captured observation.
        index: Reported zero-based logical position, when available.
        outline_level: Reported native outline depth, when available.
        cells: Ordered plans for the original captured cells.
        action_refs: Original executable action owners retained in this collection.
        partial: Whether the source or represented members have unresolved collection gaps.
    """

    ref: str
    index: int | None
    outline_level: int | None
    cells: tuple[TableCellPlan, ...]
    action_refs: tuple[str, ...]
    partial: bool = False


@dataclass(frozen=True)
class TableColumnPlan:
    """An independently referenced column and its proven ordered cell membership.

    Attributes:
        ref: Original element reference within this captured observation.
        index: Reported zero-based logical position, when available.
        cell_refs: Ordered original cells belonging to this reported column.
        action_refs: Original executable action owners retained in this collection.
    """

    ref: str
    index: int
    cell_refs: tuple[str, ...]
    action_refs: tuple[str, ...]


@dataclass(frozen=True)
class CollectionMemberPrefix:
    """Exact local fields represented by a collection's common header.

    State indices refer to the unchanged member AtomPlan. Every other state stays
    on that member, including selection and focus differences within a column.

    Attributes:
        ref: Original element reference within this captured observation.
        state_indices: Zero-based positions of member states represented in the common header.
        slot_keys: Original member slot keys represented by the shared prefix.
    """

    ref: str
    state_indices: tuple[int, ...] = ()
    slot_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectionPrefixPlan:
    """Common role, state intersection and structural slots for named members.

    Header fields carry all participating source facts. Members describe exactly
    which original fields the header represents; their complete atoms remain
    available for independent reading, accounting and incremental delivery.

    Attributes:
        member_refs: Ordered original collection members represented by the prefix.
        role: Source-backed role selected for this reading unit.
        states: Ordered state declarations with their original source evidence.
        slots: Ordered scalar fields retained for terminal formatting.
        members: Per-source presentation records retaining each independent member.
    """

    member_refs: tuple[str, ...]
    role: AtomRole
    states: tuple[AtomState, ...]
    slots: tuple[AtomSlot, ...]
    members: tuple[CollectionMemberPrefix, ...]


@dataclass(frozen=True)
class CollectionDetailField:
    """An exact structural atom slot retained in same-capture element details.

    Attributes:
        ref: Original element reference within this captured observation.
        slot: Exact structural field slot retained in captured details.
    """

    ref: str
    slot: AtomSlot


@dataclass(frozen=True)
class TablePlan:
    """A logical table or outline whose contents retain their original atom identities.

    Coordinates and extents are zero-based native structure facts. Missing cells
    are never manufactured. The renderer can serialize the planned rows while
    preserving each cell's full atom content and independent descendants.

    Attributes:
        ref: Original table or outline identity.
        kind: Native logical table or a separately proven native outline.
        scope: Original session, observation and window/root identities.
        rows: Ordered captured rows with all original cells and descendants.
        columns: Independently identified columns with exact source membership.
        remaining_refs: Other direct table children, kept outside the row layout.
        consumed_refs: Original row, cell and descendant identities handled by rows.
        action_refs: Original action owners anywhere in the table's own row content.
        row_count: Reported total row count, or None when it was not provided.
        column_count: Reported total column count, or None when it was not provided.
        partial: Captured scope or members have known coverage limitations.
        layout: A compact table for short cell branches, otherwise complete row blocks.
        column_indices: Captured zero-based cell starting columns in logical order.
        column_labels: Complete native column names, or None for neutral numbering.
        row_prefix: Common row role and exact shared fields with per-row omissions.
        column_prefixes: Common cell content for each logical column. State
            differences remain in each member rather than disabling role sharing.
        detail_fields: Exact positional metadata omitted only from a simple outline's
            full view; original atoms and same-capture native details retain each value.
    """

    ref: str
    kind: Literal["table", "outline"]
    scope: tuple[str, str, str]
    rows: tuple[TableRowPlan, ...]
    columns: tuple[TableColumnPlan, ...]
    remaining_refs: tuple[str, ...]
    consumed_refs: tuple[str, ...]
    action_refs: tuple[str, ...]
    row_count: int | None
    column_count: int | None
    partial: bool
    layout: Literal["table", "rows", "outline"] = "table"
    column_indices: tuple[int, ...] = ()
    column_labels: tuple[str | None, ...] = ()
    row_prefix: CollectionPrefixPlan | None = None
    column_prefixes: tuple[CollectionPrefixPlan | None, ...] = ()
    detail_fields: tuple[CollectionDetailField, ...] = ()


@dataclass(frozen=True)
class CollectionRejection:
    """A collection kept as its existing tree because local evidence is insufficient.

    Attributes:
        ref: Original element reference within this captured observation.
        reason: Source-specific explanation for the selected decision or coverage gap.
    """

    ref: str
    reason: str


@dataclass(frozen=True)
class CollectionRequest:
    """Structured interface and atoms with the exact native snapshot behind them.

    Attributes:
        interface: Structured block forest selected for this captured view.
        atoms: Source-bound atomic fields, states, actions, and coverage.
        native: Immutable normalized native observation.
        refs: Published element references mapped to original native object tokens.
    """

    interface: InterfaceDocument
    atoms: AtomDocument
    native: NativeObservation
    refs: Mapping[str, str]


@dataclass(frozen=True)
class CollectionEvidence:
    """Existing pure collection proofs retained for structural layout planning.

    Attributes:
        request: Immutable inputs and policy for this local planning stage.
        outlines: Complete native row-membership proofs indexed by collection reference.
        grids: Original logical table coordinates and cell containment evidence.
    """

    request: CollectionRequest
    outlines: Mapping[str, RowCollection]
    grids: tuple[NativeTableGrid, ...]


@dataclass(frozen=True)
class CollectionDocument:
    """Frozen plans and local fallback reasons, separate from execution bindings.

    Attributes:
        tables: Selected collection plans keyed by original table or outline reference.
        rejected: Collection candidates retained with their explicit rejection reasons.
    """

    tables: Mapping[str, TablePlan]
    rejected: tuple[CollectionRejection, ...] = ()
