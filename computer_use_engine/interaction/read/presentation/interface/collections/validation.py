# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contract validation for immutable native collection layout plans."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.collections.model import (
    CollectionDocument,
    CollectionPrefixPlan,
)


def _validate_prefix(prefix: CollectionPrefixPlan | None, refs: tuple[str, ...]) -> None:
    """Require an exact set of original owners and explicit local field omissions."""
    if prefix is None:
        return
    if prefix.member_refs != refs or tuple(member.ref for member in prefix.members) != refs:
        raise ValueError("A collection prefix changed its original member sequence")
    keys = tuple(slot.key for slot in prefix.slots)
    if len(refs) < 2 or len(set(refs)) != len(refs) or len(set(keys)) != len(keys):
        raise ValueError("A collection prefix lacks distinct members or fields")
    for member in prefix.members:
        indices = member.state_indices
        if (
            len(indices) != len(prefix.states)
            or len(set(indices)) != len(indices)
            or any(type(index) is not int or index < 0 for index in indices)
            or member.slot_keys != keys
        ):
            raise ValueError("A collection prefix has inconsistent member field destinations")


def validate_collections(document: CollectionDocument) -> None:
    """Require unique original members, valid extents and exact column membership.

    Raises:
        ValueError: A plan has fabricated, repeated or inconsistent members or extents.
    """
    for ref, plan in document.tables.items():
        if ref != plan.ref or not plan.rows:
            raise ValueError("A table plan lacks its original identity or rows")
        if plan.layout in ("table", "outline"):
            columns = tuple(cell.column for cell in plan.rows[0].cells)
            if any(
                tuple(cell.column for cell in row.cells) != columns
                or any(cell.row_span != 1 or cell.column_span != 1 for cell in row.cells)
                for row in plan.rows
            ):
                raise ValueError("A compact collection requires aligned unmerged rows")
        members = []
        cells = {}
        for row in plan.rows:
            if not row.cells or len({cell.row for cell in row.cells}) != 1:
                raise ValueError("A logical row has inconsistent cell coordinates")
            members.append(row.ref)
            for cell in row.cells:
                if (
                    type(cell.row) is not int
                    or type(cell.column) is not int
                    or cell.row < 0
                    or cell.column < 0
                    or type(cell.row_span) is not int
                    or type(cell.column_span) is not int
                    or cell.row_span < 1
                    or cell.column_span < 1
                ):
                    raise ValueError("A cell lacks a valid original logical extent")
                if any(child not in cell.descendant_refs for child in cell.children):
                    raise ValueError("A cell lost one of its original content branches")
                if cell.inline_chain and cell.inline_chain != (cell.ref, *cell.descendant_refs):
                    raise ValueError("An inline cell chain changed its original member sequence")
                members.extend((cell.ref, *cell.descendant_refs))
                cells[cell.ref] = cell
        if tuple(members) != plan.consumed_refs or len(set(members)) != len(members):
            raise ValueError("A table layout lost or duplicated an original member")
        if set(members) & set(plan.remaining_refs) or plan.ref in members:
            raise ValueError("A table layout consumed an unrelated or parent object")
        details = tuple((field.ref, field.slot.key) for field in plan.detail_fields)
        if (
            len(set(details)) != len(details)
            or plan.detail_fields
            and (plan.layout != "outline" or plan.kind != "outline" or plan.column_indices != (0,) or plan.partial)
            or any(
                field.ref not in members
                or field.slot.key not in ("index", "disclosureLevel", "rowRange", "columnRange")
                or not field.slot.facts
                or any(
                    fact.key.source != field.ref
                    or fact.key.attribute != field.slot.key
                    or fact.value != field.slot.value
                    or type(fact.value) is not type(field.slot.value)
                    for fact in field.slot.facts
                )
                for field in plan.detail_fields
            )
        ):
            raise ValueError("An outline detail field lacks its original structural source")
        if (
            len(plan.column_indices) != len(plan.column_labels)
            or len(plan.column_indices) != len(plan.column_prefixes)
            or plan.column_indices != tuple(sorted({cell.column for cell in cells.values()}))
        ):
            raise ValueError("A table has inconsistent column prefix slots")
        _validate_prefix(plan.row_prefix, tuple(row.ref for row in plan.rows))
        for index, prefix in zip(plan.column_indices, plan.column_prefixes):
            _validate_prefix(prefix, tuple(cell.ref for row in plan.rows for cell in row.cells if cell.column == index))
        for column in plan.columns:
            expected = tuple(
                cell.ref
                for row in plan.rows
                for cell in row.cells
                if cell.column == column.index and cell.column_span == 1
            )
            if expected != column.cell_refs or column.ref not in plan.remaining_refs:
                raise ValueError("A native column no longer matches its captured cell sequence")
