# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select repeatable native collection patterns from semantic and geometric evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.patterns.model import (
    PatternCandidate,
    PatternPlan,
    PatternRow,
    PatternSelection,
)
from computer_use.interaction.read.presentation.interface.geometric.patterns.source import (
    complete_row_cells,
    inline_chain,
    item_box,
    source_keys,
    wrapper_chain,
)
from computer_use.interaction.read.presentation.interface.helpers import synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges, stable_order
from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, analyze_alignment
from computer_use.interaction.read.presentation.spatial.columns import project_columns

_COLLECTIONS = frozenset(("list", "outline", "table"))


def _grid(draft: InterfaceDraft, owner: InterfaceBlock) -> PatternPlan | None:
    """Recognize a repeated two-dimensional item matrix inside a native collection."""
    if len(owner.children) < 4 or owner.primary_ref is None:
        return None
    chains = {key: inline_chain(draft, key) for key in owner.children}
    boxes = {key: item_box(draft, key) for key in owner.children}
    runs: list[tuple[str, ...]] = []
    current: list[str] = []
    for key in owner.children:
        if chains[key] is not None and boxes[key] is not None:
            current.append(key)
        elif current:
            runs.append(tuple(current))
            current.clear()
    if current:
        runs.append(tuple(current))
    eligible = tuple(run for run in runs if len(run) >= 4)
    if not eligible:
        return None
    # Unknown-position sources keep their native place. The best measured run
    # can still form a grid without moving any member across that boundary.
    members = max(eligible, key=len)
    if len({boxes[key].space for key in members}) != 1:
        return None
    refs = {ref: key for key in members for ref in source_keys(draft, key)}
    if (
        len(refs) != sum(len(source_keys(draft, key)) for key in members)
        or len({draft.source.scopes.get(ref) for ref in (*refs, owner.primary_ref)}) != 1
    ):
        return None
    analysis = analyze_alignment(tuple(LayoutItem(key, boxes[key]) for key in members))
    columns = project_columns(analysis)
    if columns is None or len(columns.rows) < 2 or len(columns.alignments) < 2:
        return None
    # A grid needs repeated row/column anchors. A diagonal arrangement or a single
    # multicolumn row followed by unrelated content stays in its native list.
    if sum(len(row.members) >= 2 for row in columns.rows) < 2:
        return None
    ordered = tuple(key for row in columns.rows for key in row.members)
    edges = native_edges(members, refs, draft.relations)
    if edges is None or stable_order(ordered, edges) != ordered:
        return None
    if any(any(slot is None for slot in row.slots) for row in columns.rows):
        return None
    rows = tuple(
        PatternRow(
            synthetic_key("pattern-grid-row", owner.key, row.members),
            None,
            row.members,
            tuple(slot for slot in row.slots if slot is not None),
        )
        for row in columns.rows
    )
    return PatternPlan(
        owner.key,
        owner.primary_ref,
        "grid",
        rows,
        remaining_keys=tuple(key for key in owner.children if key not in members),
        column_count=len(columns.alignments),
        column_alignments=columns.alignments,
        wrapper_chains=tuple(chains[key] for key in members if len(chains[key]) > 1),
        evidence=("native-collection-items", "repeated-geometric-rows-and-columns"),
        input_keys=owner.children,
    )


def _table(draft: InterfaceDraft, owner: InterfaceBlock) -> PatternPlan | None:
    """Choose captured multi-cell native rows without requiring unread rows to expand."""
    if owner.primary_ref is None:
        return None
    rows, remaining = [], []
    for key in owner.children:
        block = draft.blocks[key]
        cells = complete_row_cells(draft, block)
        if cells is None or len(cells) < 2:
            remaining.append(key)
            continue
        rows.append(
            PatternRow(
                key, block.primary_ref, tuple(member for member, _ in cells), tuple(column for _, column in cells)
            )
        )
    if len(rows) < 2:
        return None
    memberships = Counter(row.slots for row in rows)
    dominant, count = memberships.most_common(1)[0]
    if count < 2 or count < len(rows) * 0.75:
        return None
    # Other complete rows retain their native positions rather than being forced
    # into a repeated structure with different logical columns.
    selected = tuple(row for row in rows if row.slots == dominant)
    selected_keys = {row.key for row in selected}
    remaining = tuple(key for key in owner.children if key not in selected_keys)
    return PatternPlan(
        owner.key,
        owner.primary_ref,
        "table",
        selected,
        remaining,
        max(dominant) + 1,
        ("left",) * (max(dominant) + 1),
        evidence=("native-row-and-cell-identities", "repeated-logical-columns"),
        input_keys=owner.children,
    )


def _card_row(draft: InterfaceDraft, key: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Find a native single-cell row containing several separately sourced text fields."""
    row = draft.blocks[key]
    cells = complete_row_cells(draft, row)
    if cells is None or len(cells) != 1:
        return None
    chain = wrapper_chain(draft, key)
    refs = source_keys(draft, key)
    if any(draft.evidence[ref].incomplete for ref in refs):
        return None
    content = tuple(
        ref
        for ref in refs
        if draft.projected_nodes[ref]["role"] in ("staticText", "heading")
        and any(
            isinstance(draft.projected_nodes[ref].get(field), str) and draft.projected_nodes[ref][field].strip()
            for field in ("name", "value", "description")
        )
    )
    if len(content) < 2:
        return None
    # A real row and single native cell establish the item boundary. Multiple
    # fields permit card presentation; no button is required and none is invented.
    shape = tuple(draft.projected_nodes[ref]["role"] for ref in content)
    return chain, shape


def _cards(draft: InterfaceDraft, owner: InterfaceBlock) -> PatternPlan | None:
    """Select repeated rich single-column native items, keeping heading rows separate."""
    if owner.primary_ref is None:
        return None
    candidates = {key: card for key in owner.children if (card := _card_row(draft, key)) is not None}
    if len(candidates) < 2:
        return None
    # Rich text-only notes are valid native items. Repetition distinguishes them
    # from one exceptional row while optional icons/actions remain unrestricted.
    shapes = Counter(shape for _, shape in candidates.values())
    shape, count = shapes.most_common(1)[0]
    if count < 2:
        return None
    keys = tuple(key for key in owner.children if key in candidates and candidates[key][1] == shape)
    rows = tuple(PatternRow(key, draft.blocks[key].primary_ref, draft.blocks[key].children) for key in keys)
    remaining = tuple(key for key in owner.children if key not in keys)
    return PatternPlan(
        owner.key,
        owner.primary_ref,
        "cards",
        rows,
        remaining,
        wrapper_chains=tuple(candidates[key][0] for key in keys if len(candidates[key][0]) > 1),
        evidence=("native-single-cell-items", "repeated-multifield-content"),
        input_keys=owner.children,
    )


def analyze_collection_patterns(draft: InterfaceDraft) -> tuple[PatternSelection, ...]:
    """Compare finite native-table, visual-grid and rich-card explanations.

    Args:
        draft: Captured source identities, immutable fields and local geometry.

    Returns:
        Per-collection candidate decisions, including the ordinary-tree outcome.
        Confirmed native row/column identities take priority. A repeated visual
        matrix then takes priority over a rich single-column list, which retains
        its native item order. Every rejected candidate records its requirements.
    """
    if not draft.enabled:
        return ()
    results: list[PatternSelection] = []
    explanations = {
        "table": "requires two complete native rows with repeated logical columns",
        "grid": "requires a measured whole-item run with repeated rows and columns and compatible native order",
        "cards": "requires two native single-cell rows with repeated multifield content",
    }
    priority = {"table": 3, "grid": 2, "cards": 1}
    for block in draft.blocks.values():
        ref = block.primary_ref
        if (
            ref is None
            or block.origin != "native"
            or block.layout != "tree"
            or draft.projected_nodes[ref]["role"] not in _COLLECTIONS
            or not block.children
        ):
            continue
        alternatives = (("table", _table(draft, block)), ("grid", _grid(draft, block)), ("cards", _cards(draft, block)))
        candidates = tuple(
            PatternCandidate(
                kind,
                plan is not None,
                "supported by " + ", ".join(plan.evidence) if plan is not None else explanations[kind],
                plan.evidence if plan is not None else (),
            )
            for kind, plan in alternatives
        )
        supported = tuple(plan for _, plan in alternatives if plan is not None)
        selected = (
            replace(max(supported, key=lambda plan: priority[plan.kind]), candidates=candidates) if supported else None
        )
        results.append(PatternSelection(block.key, ref, candidates, selected))
    return tuple(results)


def select_collection_patterns(draft: InterfaceDraft) -> tuple[PatternPlan, ...]:
    """Return the selected source-preserving pattern of each eligible collection."""
    return tuple(result.selected for result in analyze_collection_patterns(draft) if result.selected is not None)
