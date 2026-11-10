# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable per-reference layout facts for structured delivery comparison."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TypeAlias

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDocument

LayoutValue: TypeAlias = str | int | None | tuple["LayoutValue", ...]
BlockLayoutSignature: TypeAlias = tuple[tuple[str, LayoutValue], ...]
InterfaceLayoutSignature: TypeAlias = tuple[BlockLayoutSignature, ...]


def _block_signature(block: InterfaceBlock) -> BlockLayoutSignature:
    """Describe local organization and the exact members of a synthetic grouping.

    Native child membership already belongs to the projected hierarchy. Keeping
    it out of this path signature limits a changed visual group to its members.
    """
    signature = (
        ("key", block.key),
        ("kind", block.kind),
        ("layout", block.layout),
        ("primary_ref", block.primary_ref),
        ("source_refs", block.source_refs),
        ("children", block.children if block.primary_ref is None else ()),
        ("origin", block.origin),
        ("position", block.position),
        ("label_ref", block.label_ref),
        ("shared_prefix_ref", block.shared_prefix_ref),
        ("columns", block.columns),
        ("column_prefix_refs", block.column_prefix_refs),
        ("region_role", block.region_role),
        ("field_target_ref", block.field_target_ref),
        ("member_roles", block.member_roles),
        ("heading_style", block.heading_style),
        ("visual_unit", "control" if block.visual_unit else None),
        ("column_slots", block.column_slots),
        ("column_alignments", block.column_alignments),
    )
    return signature


def interface_layout_signatures(
    document: InterfaceDocument, *, checkpoint: Callable[[], object] | None = None
) -> Mapping[str, InterfaceLayoutSignature]:
    """Capture each real element's reading-block path for full/diff comparison.

    Signatures contain local layout and source identities along the exact block
    path, including the membership of synthetic groups. They remain stable when
    an entire window moves, another element's content changes, or a separate
    branch is regrouped. Changed row membership remains observable for its members
    before text formatting.

    Args:
        document: Validated immutable interface organization for one projected root.
        checkpoint: Original call-budget check, consulted within bounded work batches.

    Returns:
        Immutable mapping from every projected reference to its ordered block path.
    """
    if checkpoint is not None:
        checkpoint()
    work = 0

    def checked(items):
        nonlocal work
        for item in items:
            work += 1
            if checkpoint is not None and work % 128 == 0:
                checkpoint()
            yield item

    result: dict[str, InterfaceLayoutSignature] = {}
    patterns = {
        plan.owner_key: (
            ("pattern_kind", plan.kind),
            ("pattern_columns", plan.column_count),
            ("pattern_rows", tuple((row.key, row.ref, row.members, row.slots) for row in checked(plan.rows))),
            ("pattern_remaining", plan.remaining_keys),
        )
        for plan in checked(document.geometric_patterns)
    }
    pending: list[tuple[str, InterfaceLayoutSignature]] = [(key, ()) for key in checked(reversed(document.roots))]
    while pending:
        work += 1
        if checkpoint is not None and work % 128 == 0:
            checkpoint()
        # Each block and its possibly wide source/child containers share the
        # same batch counter; a single synthetic group cannot evade the budget.
        key, ancestors = pending.pop()
        block = document.blocks[key]
        current = (*ancestors, (*_block_signature(block), *patterns.get(key, ())))
        result.update((ref, current) for ref in checked(block.source_refs))
        pending.extend((child, current) for child in checked(reversed(block.children)))
    return MappingProxyType(result)
