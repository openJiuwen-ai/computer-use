# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Narrow read-only queries for collection item and cell pattern evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources
from computer_use.interaction.read.presentation.spatial.geometry import Box

_WRAPPERS = frozenset(("group", "row", "cell"))


def native_range(node: Mapping[str, Any], key: str) -> tuple[int, int] | None:
    """Read a reported logical extent without deriving it from physical positions."""
    value = node.get(key)
    if not isinstance(value, Mapping):
        return None
    start, length = value.get("location"), value.get("length")
    return (start, length) if type(start) is int and start >= 0 and type(length) is int and length > 0 else None


def original_edge(draft: InterfaceDraft, parent: str, child: str) -> bool:
    """Confirm one retained source edge independently of unrelated partial siblings."""
    if draft.projected_parents.get(child) != parent or draft.source.parents.get(child) != parent:
        return False
    node = draft.source.native_nodes.get(parent)
    return node is not None and tuple(item["ref"] for item in node.get("children", ())) == (child,)


def wrapper_chain(draft: InterfaceDraft, key: str) -> tuple[str, ...]:
    """Return a real single-child wrapper path with the final member included."""
    chain = [key]
    seen = {key}
    while True:
        block = draft.blocks[chain[-1]]
        ref = block.primary_ref
        if (
            ref is None
            or block.source_refs != (ref,)
            or len(block.children) != 1
            or block.layout not in ("tree", "record-path", "chain")
            or draft.projected_nodes[ref]["role"] not in _WRAPPERS
            or draft.evidence[ref].incomplete
        ):
            break
        child_key = block.children[0]
        child = draft.blocks[child_key]
        if child_key in seen or child.primary_ref is None or not original_edge(draft, ref, child.primary_ref):
            break
        chain.append(child_key)
        seen.add(child_key)
    return tuple(chain)


def source_keys(draft: InterfaceDraft, key: str) -> tuple[str, ...]:
    """Retain the exact original references owned by a complete candidate branch."""
    return block_sources(key, draft.blocks)


def item_box(draft: InterfaceDraft, key: str) -> Box | None:
    """Measure one original item without including an outer collection rectangle."""
    block = draft.blocks[key]
    if block.primary_ref is None:
        return None
    return draft.source.geometry.boxes.get(block.primary_ref)


def inline_chain(draft: InterfaceDraft, key: str) -> tuple[str, ...] | None:
    """Recognize a complete short item path suitable for one grid cell."""
    chain = wrapper_chain(draft, key)
    last = draft.blocks[chain[-1]]
    if last.children or any(draft.blocks[member].primary_ref is None for member in chain):
        return None
    refs = tuple(draft.blocks[member].primary_ref for member in chain)
    if len({draft.source.scopes.get(ref) for ref in refs}) != 1:
        return None
    for ref in refs:
        evidence = draft.evidence[ref]
        if evidence.hidden or evidence.incomplete:
            return None
        node = draft.projected_nodes[ref]
        if node["role"] in ("window", "sheet", "webArea", "document", "scrollArea", "table", "outline", "list"):
            return None
        if draft.atoms is not None:
            atom = draft.atoms.atoms.get(ref)
            if (
                atom is None
                or atom.coverage.partial
                or any(
                    slot.placement == "block"
                    or isinstance(slot.value, str)
                    and any(c in slot.value for c in ("\r", "\n"))
                    for slot in atom.slots
                )
            ):
                return None
    return chain


def complete_row_cells(draft: InterfaceDraft, block: InterfaceBlock) -> tuple[tuple[str, int], ...] | None:
    """Read independently captured native cells of one complete original row."""
    ref = block.primary_ref
    if (
        ref is None
        or draft.projected_nodes[ref]["role"] != "row"
        or not block.children
        or draft.evidence[ref].incomplete
    ):
        return None
    cells: list[tuple[str, int]] = []
    rows: set[int] = set()
    for key in block.children:
        cell = draft.blocks[key]
        if cell.primary_ref is None:
            return None
        node = draft.projected_nodes[cell.primary_ref]
        row, column = native_range(node, "rowRange"), native_range(node, "columnRange")
        if (
            node["role"] not in ("cell", "columnHeader")
            or row is None
            or column is None
            or row[1] != 1
            or column[1] != 1
            or draft.evidence[cell.primary_ref].incomplete
            or draft.projected_parents.get(cell.primary_ref) != ref
            or draft.source.scopes.get(cell.primary_ref) != draft.source.scopes.get(ref)
        ):
            return None
        rows.add(row[0])
        cells.append((key, column[0]))
    indices = tuple(column for _, column in cells)
    if len(rows) != 1 or indices != tuple(sorted(set(indices))):
        return None
    return tuple(cells)
