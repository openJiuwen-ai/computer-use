# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize local visual rows while retaining each member's original identity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise, same_scope, synthetic_key
from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, same_row
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import ordered_ancestry, reading_scope
from computer_use.interaction.read.presentation.spatial.geometry import Box
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.geometric.units_support import prepared_unit_rows, row_unit

_PARENTS = frozenset(("window", "sheet", "group", "splitGroup", "tabPanel", "scrollArea", "navigation"))
_PRESERVED_LAYOUTS = frozenset(
    ("records", "record-blocks", "record-path", "compact-list", "chain", "inline", "visual-row")
)


def local_parent(parent: InterfaceBlock, draft: InterfaceDraft, *, minimum_children: int = 2) -> bool:
    """Accept a complete local scope or an already-established synthetic field."""
    if (
        parent.visual_unit
        or parent.layout in _PRESERVED_LAYOUTS
        or parent.kind in ("table", "collection", "record")
        or len(parent.children) < minimum_children
    ):
        return False
    if parent.primary_ref is None:
        return parent.kind in ("field", "region")
    ref = parent.primary_ref
    return (
        draft.projected_nodes[ref]["role"] in _PARENTS
        and (
            not draft.projected_nodes[ref].get("layoutGroups")
            or prepared_unit_rows(parent, draft, draft.blocks) is not None
        )
        and not ordered_ancestry(ref, draft)
        and not draft.evidence[ref].incomplete
        and not draft.evidence[ref].hidden
    )


def member_box(key: str, draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> Box | None:
    """Measure one complete row unit while retaining all original source references."""
    unit = row_unit(key, draft, blocks)
    return unit.box if unit is not None else None


def source_partition(ref: str, draft: InterfaceDraft) -> str | None:
    """Retain native multi-child containment after transparent wrappers collapse."""
    sources = draft.geometric_sources

    def parent_of(source: str) -> str | None:
        if sources is None:
            return draft.source.parents.get(source)
        report = sources.parent_reports.get(source)
        if report is None or report.status in ("unknown", "conflict"):
            return None
        return sources.original_parent(source)

    current = parent_of(ref)
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        node = (sources.semantic_nodes if sources is not None else draft.source.native_nodes).get(current)
        if node is None:
            return None
        read = node.get("childrenRead", len(node.get("children", ())))
        total = node.get("childCount", read)
        if (
            node.get("partialReason")
            or node.get("visible") is False
            or node.get("showing") is False
            or node.get("offscreen") is True
            or isinstance(read, int)
            and isinstance(total, int)
            and read < total
        ):
            return None
        if len(node.get("children", ())) != 1:
            return current
        current = parent_of(current)
    return None


def row_geometry(
    keys: Sequence[str],
    draft: InterfaceDraft,
    blocks: Mapping[str, InterfaceBlock],
    *,
    require_source_order: bool = True,
) -> bool:
    """Require a common vertical band and geometrically distinct horizontal members.

    Native bounds may include control padding. A small horizontal overlap is
    retained as a row when the centres and left edges still distinguish every
    member. Containment and coincident controls provide no such evidence.
    Row discovery may ignore enumeration direction; caption interpretation keeps
    the stricter default until the final order stage handles native precedence.
    """
    units = tuple(row_unit(key, draft, blocks) for key in keys)
    if len(set(keys)) != len(keys) or any(unit is None for unit in units):
        return False
    refs = tuple(ref for unit in units if unit is not None for ref in unit.sources)
    partitions = tuple(source_partition(ref, draft) for ref in refs)
    if (
        len(set(refs)) != len(refs)
        or not same_scope(draft, refs)
        or not same_region(draft, refs)
        or len({reading_scope(ref, draft) for ref in refs}) != 1
        or None in partitions
        or len(set(partitions)) != 1
    ):
        return False
    boxes = tuple(unit.box for unit in units if unit is not None)
    if len({box.space for box in boxes}) != 1:
        return False
    return same_row(tuple(LayoutItem(key, box) for key, box in zip(keys, boxes)), require_order=require_source_order)


def group_visual_rows(draft: InterfaceDraft) -> InterfaceDraft:
    """Group short sibling objects by local line geometry, including inside fields.

    Args:
        draft: Current blocks and immutable observed geometry, facts and targets.

    Returns:
        A draft whose new synthetic rows preserve the existing member order.
        Every original block remains the sole owner of its source references.
    """
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    for parent_key, parent in draft.blocks.items():
        if not local_parent(parent, draft):
            continue
        output: list[str] = []
        run: list[str] = []
        prepared_rows = prepared_unit_rows(parent, draft, blocks)
        candidate_rows = {key: index for index, members in enumerate(prepared_rows or ()) for key in members}

        def flush() -> None:
            units = tuple(row_unit(key, draft, blocks) for key in run)
            roles = {draft.projected_nodes[unit.target_ref]["role"] for unit in units if unit is not None}
            visual = any(blocks[key].visual_unit for key in run)
            if len(run) < 2 or parent.kind != "field" and not (len(roles) > 1 or roles == {"staticText"} or visual):
                output.extend(run)
            else:
                key = synthetic_key("visual-row", parent_key, run)
                blocks[key] = InterfaceBlock(key, "content", None, (), tuple(run), layout="visual-row", origin="visual")
                output.append(key)
                decisions.append("visual-row:" + ",".join(unit.target_ref for unit in units if unit is not None))
            run.clear()

        for key in parent.children:
            child = blocks[key]
            if (
                parent.label_ref is not None
                and child.primary_ref == parent.label_ref
                or member_box(key, draft, blocks) is None
            ):
                flush()
                output.append(key)
                continue
            if run and candidate_rows and candidate_rows[run[-1]] != candidate_rows[key]:
                flush()
            if run and not row_geometry((*run, key), draft, blocks, require_source_order=False):
                flush()
            run.append(key)
        flush()
        if tuple(output) != parent.children:
            blocks[parent_key] = replace(parent, children=tuple(output))
    return revise(draft, blocks, *decisions)
