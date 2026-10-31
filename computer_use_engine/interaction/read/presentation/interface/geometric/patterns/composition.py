# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Materialize selected collection patterns without changing source atoms or bindings."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.patterns.selection import select_collection_patterns
from computer_use.interaction.read.presentation.interface.helpers import revise, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft


def apply_patterns(draft: InterfaceDraft) -> InterfaceDraft:
    """Freeze collection layout choices and retain every unselected original child.

    Grid rows contain whole original item branches. Rich card rows preserve their
    native single-child wrapper paths on one line; their remaining content and
    independent controls stay under the same original item. Logical table plans
    are consumed by the native collection content planner after layout is frozen.
    """
    plans = select_collection_patterns(draft)
    if not plans:
        return replace(draft, geometric_patterns=())
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    for plan in plans:
        owner = blocks[plan.owner_key]
        for path in plan.wrapper_chains:
            for key in path[:-1]:
                blocks[key] = replace(blocks[key], layout="record-path")
        if plan.kind == "grid":
            row_keys = []
            for row in plan.rows:
                blocks[row.key] = InterfaceBlock(
                    row.key,
                    "content",
                    None,
                    (),
                    row.members,
                    layout="visual-row",
                    origin="visual",
                    column_slots=row.slots,
                )
                row_keys.append(row.key)
            key = synthetic_key("collection-grid", owner.key, row_keys)
            blocks[key] = InterfaceBlock(
                key,
                "content",
                None,
                (),
                tuple(row_keys),
                layout="visual-grid",
                origin="visual",
                columns=tuple(str(index) for index in range(plan.column_count)),
                column_prefix_refs=(None,) * plan.column_count,
                column_alignments=plan.column_alignments,
            )
            selected = {member for row in plan.rows for member in row.members}
            first = next(child for child in owner.children if child in selected)
            blocks[owner.key] = replace(
                owner,
                children=tuple(
                    key if child == first else child
                    for child in owner.children
                    if child == first or child not in selected
                ),
            )
        elif plan.kind == "cards":
            for row in plan.rows:
                blocks[row.key] = replace(blocks[row.key], kind="record")
            selected = {row.key for row in plan.rows}
            output: list[str] = []
            run: list[str] = []

            def flush() -> None:
                if len(run) >= 2:
                    key = synthetic_key("native-card-list", owner.key, run)
                    blocks[key] = InterfaceBlock(
                        key, "collection", None, (), tuple(run), layout="record-blocks", origin="visual"
                    )
                    output.append(key)
                else:
                    output.extend(run)
                run.clear()

            for child in owner.children:
                if child in selected:
                    run.append(child)
                else:
                    flush()
                    output.append(child)
            flush()
            blocks[owner.key] = replace(owner, children=tuple(output))
        decisions.append("geometric-pattern:" + plan.kind + ":" + plan.owner_ref)
    result = revise(draft, blocks, *decisions)
    return replace(result, geometric_patterns=plans)
