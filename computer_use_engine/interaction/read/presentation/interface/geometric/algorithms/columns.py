# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select compact column templates from completed local visual rows."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.units_support import row_unit
from computer_use.interaction.read.presentation.interface.helpers import (
    CONTROL_ROLES,
    FIELD_ROLES,
    prefix_signature,
    revise,
    synthetic_key,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.constraints import native_edges, stable_order
from computer_use.interaction.read.presentation.interface.regions.scope import comparable_members
from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, analyze_alignment
from computer_use.interaction.read.presentation.spatial.columns import project_columns
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources


def _column_row(row: InterfaceBlock) -> bool:
    """Accept a complete synthetic row whose original cells can form columns."""
    if row.primary_ref is not None or row.source_refs:
        return False
    return row.layout in ("visual-row", "inline") and bool(row.children)


def plan_columns(draft: InterfaceDraft) -> InterfaceDraft:
    """Project complete visual units, then select a record or plain grid template.

    Field rows already express label/control meaning. This stage handles remaining
    repeated local rows, including composite controls, absent cells and floating
    members. Geometry establishes slots independently of roles. Existing atomic
    record evidence permits a shared column heading; other layouts keep complete
    units in a plain grid. Every source remains at its original target.

    Args:
        draft: Completed local rows and immutable source facts before final validation.

    Returns:
        The same source graph with compact record templates and discrete column slots.
    """
    if not draft.enabled:
        return draft
    blocks, decisions = dict(draft.blocks), []

    def project_run(parent: InterfaceBlock, row_keys: tuple[str, ...]) -> str | None:
        rows = tuple(blocks[key] for key in row_keys)
        if len(rows) < 2 or any(not _column_row(row) for row in rows):
            return None
        keys = tuple(key for row in rows for key in row.children)
        refs = tuple(ref for key in keys for ref in block_sources(key, blocks))
        if (
            len(keys) != len(set(keys))
            or not same_region(draft, refs)
            or comparable_members(keys, draft, blocks) is None
        ):
            return None
        units = {key: row_unit(key, draft, blocks) for key in keys}
        if any(unit is None for unit in units.values()):
            return None
        analysis = analyze_alignment(
            tuple(LayoutItem(key, unit.box) for key, unit in units.items() if unit is not None)
        )
        projected = project_columns(analysis)
        if projected is None or tuple(row.members for row in projected.rows) != tuple(row.children for row in rows):
            return None
        roles = []
        for row in projected.rows:
            roles.extend(
                draft.projected_nodes[units[key].target_ref]["role"]
                for key, slot in zip(row.members, row.slots) if slot is not None
            )
        atomic = all(blocks[key].primary_ref is not None and not blocks[key].children for key in keys)
        record = atomic and (
            parent.layout == "records"
            or (
                any(role in ("staticText", "heading") for role in roles)
                and any(role in CONTROL_ROLES | FIELD_ROLES for role in roles)
            )
        )
        if (
            not record
            and all(row.layout == "inline" and row.shared_prefix_ref is not None for row in rows)
            and all(len(row.slots) == len(projected.alignments) and None not in row.slots for row in projected.rows)
        ):
            # Complete homogeneous rows already share a compact role heading.
            # Their existing matrix needs no additional slot or record wrapper.
            return None
        owners = {ref: key for key in keys for ref in units[key].sources}
        edges = native_edges(keys, owners, draft.relations)
        if edges is None or stable_order(keys, edges) != keys:
            return None
        if any(
            relation.kind in ("flows_to", "flows_from")
            and set((relation.source, *relation.targets)) & owners.keys()
            and not set((relation.source, *relation.targets)) <= owners.keys()
            for relation in draft.relations
        ):
            return None
        columns = []
        for column in range(len(projected.alignments)):
            column_refs = []
            for row in projected.rows:
                column_refs.extend(
                    units[key].target_ref for key, slot in zip(row.members, row.slots) if slot == column
                )
            refs = tuple(column_refs)
            # Repeated records have matching cell meanings. A form containing
            # a date, a duration and a toggle is still a set of distinct fields.
            if parent.layout != "records" and len({draft.projected_nodes[ref]["role"] for ref in refs}) != 1:
                record = False
            columns.append(refs)
        prefixes = []
        labels = []
        for column, refs in enumerate(columns):
            prefix = (
                refs[0]
                if all(prefix_signature(draft, ref) == prefix_signature(draft, refs[0]) for ref in refs) and record
                else None
            )
            prefixes.append(prefix)
            labels.append(
                "Actions"
                if all(draft.projected_nodes[ref]["role"] in CONTROL_ROLES for ref in refs)
                else f"Content {column + 1}"
            )
        for row, projected_row in zip(rows, projected.rows):
            blocks[row.key] = replace(
                row,
                kind="record" if record else "content",
                layout="inline" if record else "visual-row",
                shared_prefix_ref=None,
                column_slots=projected_row.slots,
            )
        fields = dict(columns=tuple(labels), column_prefix_refs=tuple(prefixes), column_alignments=projected.alignments)
        if parent.layout == "records":
            key = parent.key
            blocks[key] = replace(parent, **fields)
        else:
            key = synthetic_key("geometric-columns" if record else "geometric-grid", parent.key, keys)
            blocks[key] = InterfaceBlock(
                key,
                "collection" if record else "content",
                None,
                (),
                row_keys,
                layout="records" if record else "visual-grid",
                origin="visual",
                **fields,
            )
        decisions.append(("geometric-columns:" if record else "geometric-grid:") + parent.key)
        return key

    def flush(owner: InterfaceBlock, pending_rows: list[str], assembled: list[str]) -> None:
        projected = project_run(owner, tuple(pending_rows))
        assembled.extend((projected,) if projected is not None else pending_rows)
        pending_rows.clear()

    for parent_key, parent in draft.blocks.items():
        if parent.kind in ("field", "table", "record") or parent.layout not in ("tree", "records"):
            continue
        if parent.layout == "records":
            project_run(parent, parent.children)
            continue
        output, run = [], []

        for child in parent.children:
            row = blocks[child]
            if _column_row(row):
                if run and not same_region(draft, (ref for key in (*run, child) for ref in block_sources(key, blocks))):
                    flush(parent, run, output)
                run.append(child)
            else:
                flush(parent, run, output)
                output.append(child)
        flush(parent, run, output)
        if tuple(output) != parent.children:
            blocks[parent_key] = replace(parent, children=tuple(output))
    return revise(draft, blocks, *decisions)
