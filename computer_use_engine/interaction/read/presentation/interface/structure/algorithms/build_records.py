# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Promote proven visual rows to records while preserving every real member."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import (
    CONTROL_ROLES,
    element_key,
    prefix_signature,
    revise,
    same_scope,
    single_line,
    synthetic_key,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import comparable_members, ordered_ancestry
from computer_use.interaction.read.presentation.spatial.rows import atomic_rows
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Build rectangular record blocks from previously verified local geometric groups."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    for ref in draft.order:
        node = draft.projected_nodes[ref]
        groups = tuple(node.get("layoutGroups", ()))
        if not groups and node["role"] in ("window", "sheet", "group", "splitGroup", "tabPanel", "scrollArea"):
            children = tuple(node.get("children", ()))
            parent = blocks[element_key(ref)]
            if (
                not draft.evidence[ref].incomplete
                and not ordered_ancestry(ref, draft)
                and parent.children == tuple(element_key(child) for child in children)
                and all(draft.source.parents.get(child) == ref for child in children)
                and not any(draft.evidence[child].boundary for child in children)
                and comparable_members(parent.children, draft, blocks) is not None
            ):
                groups = atomic_rows(
                    tuple(draft.projected_nodes[child] for child in children), draft.source.geometry, minimum_rows=1
                )
        if not groups or any(group.get("kind") != "row" for group in groups):
            continue
        rows = tuple(tuple(group.get("members", ())) for group in groups)
        width = len(rows[0])
        if not 2 <= width <= 8 or any(len(row) != width for row in rows):
            continue
        members = tuple(member for row in rows for member in row)
        if len(set(members)) != len(members) or members != tuple(node.get("children", ())):
            continue
        if (
            not same_scope(draft, (ref, *members))
            or not same_region(draft, members)
            or any(
                draft.evidence[member].incomplete
                or draft.evidence[member].hidden
                or draft.projected_nodes[member].get("children")
                or not single_line(draft.projected_nodes[member])
                for member in members
            )
        ):
            continue
        geometry = draft.source.geometry.boxes
        if any(member not in geometry for member in members):
            continue
        if len({geometry[member].space for member in members}) != 1:
            continue
        parent = blocks[element_key(ref)]
        if parent.children != tuple(element_key(member) for member in members):
            continue
        columns: list[str] = []
        column_prefixes: list[str | None] = []
        for column in range(width):
            refs = tuple(row[column] for row in rows)
            values = tuple(draft.projected_nodes[member] for member in refs)
            columns.append(
                "Actions" if all(value["role"] in CONTROL_ROLES for value in values) else f"Content {column + 1}"
            )
            signature = prefix_signature(draft, refs[0])
            column_prefixes.append(
                refs[0] if all(prefix_signature(draft, member) == signature for member in refs) else None
            )
        record_keys = []
        for row in rows:
            key = synthetic_key("record", ref, row)
            blocks[key] = InterfaceBlock(
                key, "record", None, (), tuple(element_key(member) for member in row), layout="inline", origin="visual"
            )
            record_keys.append(key)
        if len(rows) == 1:
            blocks[element_key(ref)] = replace(parent, children=tuple(record_keys))
            decisions.append("visual-row:" + ref)
            continue
        collection_key = synthetic_key("records", ref, members)
        blocks[collection_key] = InterfaceBlock(
            collection_key,
            "collection",
            None,
            (),
            tuple(record_keys),
            layout="records",
            origin="visual",
            columns=tuple(columns),
            column_prefix_refs=tuple(column_prefixes),
        )
        blocks[element_key(ref)] = replace(parent, children=(collection_key,))
        decisions.append("visual-records:" + ref)
    return revise(draft, blocks, *decisions)
