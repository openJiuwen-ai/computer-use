# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable selected collection layouts, separate from native collection facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class PatternRow:
    """One display row whose members keep their original source blocks.

    Attributes:
        key: Existing native row block, or the deterministic key of a visual row.
        ref: Original native row identity; visual rows have no operation identity.
        members: Complete original item or cell block identities in display order.
        slots: Logical visual columns for members; empty for a standalone card.
    """

    key: str
    ref: str | None
    members: tuple[str, ...]
    slots: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """One finite local explanation and its explicit evidence decision.

    Attributes:
        kind: Local table, grid, card or list explanation being considered.
        supported: Whether captured structure satisfies the pattern's conditions.
        reason: Diagnostic explanation of the decision.
        evidence: Facts or layout mechanisms supporting an accepted candidate.
    """

    kind: Literal["grid", "table", "cards", "list"]
    supported: bool
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PatternPlan:
    """A selected local pattern with exact membership and explicit remaining content.

    Attributes:
        owner_key: Original collection block whose contents are organized.
        owner_ref: Original collection reference; no synthetic input target is created.
        kind: The selected visual grid, native table, card list or ordinary list.
        rows: Captured data rows or complete visual item rows selected by this plan.
        remaining_keys: Original direct children outside the selected rows. Unread
            native rows and unrelated controls remain independently represented.
        column_count: Number of proven visual slots, or logical native columns.
        column_alignments: Selected left, right or centre alignment per visual slot.
        wrapper_chains: Original single-child paths that may share a display line.
            Each member retains its own atom, native fields and action ownership.
        evidence: Mechanism evidence for diagnostics, independent of application names.
        candidates: Evidence decisions for every compared layout explanation.
        input_keys: Exact original direct children before visual materialization;
            selected rows and remaining members must partition this sequence.
    """

    owner_key: str
    owner_ref: str
    kind: Literal["grid", "table", "cards", "list"]
    rows: tuple[PatternRow, ...]
    remaining_keys: tuple[str, ...] = ()
    column_count: int = 0
    column_alignments: tuple[Literal["left", "right", "center"], ...] = ()
    wrapper_chains: tuple[tuple[str, ...], ...] = ()
    evidence: tuple[str, ...] = ()
    candidates: tuple[PatternCandidate, ...] = ()
    input_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PatternSelection:
    """Compared layout candidates, including collections kept in their ordinary tree.

    Attributes:
        owner_key: Existing collection block that owns the candidates.
        owner_ref: Original collection reference, independent of display grouping.
        candidates: Evaluated alternatives and their individual evidence.
        selected: Chosen plan, or None when the ordinary source layout is retained.
    """

    owner_key: str
    owner_ref: str
    candidates: tuple[PatternCandidate, ...]
    selected: PatternPlan | None
