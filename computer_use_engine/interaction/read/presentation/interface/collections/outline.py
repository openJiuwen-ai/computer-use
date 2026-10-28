# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose a row-first presentation for complete single-column native outlines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from .model import CollectionDetailField, CollectionPrefixPlan, CollectionRequest, TablePlan


def select_outline_layout(plan: TablePlan, request: CollectionRequest) -> TablePlan:
    """Keep every real row/cell while moving repetitive structure into captured details.

    Different outline levels remain inline because they express meaningful tree
    hierarchy. The row order, names, states, values, actions and all target refs
    remain directly visible. Grids, merged cells and long branches retain their
    existing table or tree presentation.
    """
    if (
        plan.kind != "outline"
        or plan.layout != "table"
        or plan.partial
        or plan.column_indices != (0,)
        or plan.column_count not in (None, 1)
        or any(len(row.cells) != 1 or row.cells[0].row_span != 1 or row.cells[0].column_span != 1 for row in plan.rows)
    ):
        return plan
    levels = {row.outline_level for row in plan.rows if row.outline_level is not None}
    fields = []

    def retain_detail(ref: str, key: str, value: object) -> None:
        slot = request.atoms.atoms[ref].slot(key)
        if (
            slot is not None
            and type(slot.value) is type(value)
            and slot.value == value
            and slot.facts
            and all(
                fact.key.source == ref
                and fact.key.attribute == key
                and type(fact.value) is type(value)
                and fact.value == value
                for fact in slot.facts
            )
        ):
            fields.append(CollectionDetailField(ref, slot))

    for row in plan.rows:
        if row.index is not None:
            retain_detail(row.ref, "index", row.index)
        if len(levels) == 1 and row.outline_level is not None:
            retain_detail(row.ref, "disclosureLevel", row.outline_level)
        cell = row.cells[0]
        for key, location, length in (
            ("rowRange", cell.row, cell.row_span),
            ("columnRange", cell.column, cell.column_span),
        ):
            slot = request.atoms.atoms[cell.ref].slot(key)
            if (
                slot is not None
                and isinstance(slot.value, Mapping)
                and dict(slot.value) == {"location": location, "length": length}
            ):
                retain_detail(cell.ref, key, slot.value)
    if not fields:
        return plan
    deferred = {(item.ref, item.slot.key) for item in fields}

    def visible_prefix(prefix: CollectionPrefixPlan | None) -> CollectionPrefixPlan | None:
        if prefix is None:
            return None
        slots = tuple(
            slot for slot in prefix.slots if not all((ref, slot.key) in deferred for ref in prefix.member_refs)
        )
        keys = tuple(slot.key for slot in slots)
        return replace(prefix, slots=slots, members=tuple(replace(member, slot_keys=keys) for member in prefix.members))

    return replace(
        plan,
        layout="outline",
        detail_fields=tuple(fields),
        row_prefix=visible_prefix(plan.row_prefix),
        column_prefixes=tuple(visible_prefix(prefix) for prefix in plan.column_prefixes),
    )
