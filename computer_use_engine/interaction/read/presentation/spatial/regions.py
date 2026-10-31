# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize parallel source regions without changing their reading order."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.policy import same_blocking
from computer_use.interaction.read.presentation.ir import ProjectionView
from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.spatial.geometry import Box, GeometryIndex
from computer_use.interaction.read.presentation.spatial.policy import (
    available_region,
    complete_visible,
    source_region,
    visible_source_path,
)


_LOGICAL_COLLECTIONS = frozenset(("list", "outline", "table", "row", "cell", "column"))
_DIVIDERS = frozenset(("separator", "growArea"))


def parallel_regions(
    view: ProjectionView,
    parent: NodeView,
    geometry: GeometryIndex,
) -> tuple[tuple[NodeView, Box], ...]:
    """Return two or three separate columns in left-to-right geometric order.

    Source children remain in their existing order. Thin native dividers between
    the columns are allowed and retain their independent display and actions.
    Logical collections and unknown extra children keep their native structure.
    """
    if not available_region(parent) or not visible_source_path(view, parent):
        return ()
    ancestor: str | None = parent["ref"]
    while ancestor is not None:
        if view.native_nodes[ancestor]["role"] in _LOGICAL_COLLECTIONS:
            return ()
        ancestor = view.parents.get(ancestor)

    regions, dividers = [], []
    for child in parent["children"]:
        if (
            view.scopes[child["ref"]] != view.scopes[parent["ref"]]
            or not same_blocking(parent, child)
            or not complete_visible(child)
            or not visible_source_path(view, child)
        ):
            return ()
        box = geometry.boxes.get(child["ref"])
        if box is None:
            return ()
        if available_region(child) and child["children"]:
            regions.append((child, box))
        elif child["role"] in _DIVIDERS:
            dividers.append((child, box))
        else:
            return ()
    if len(regions) not in (2, 3) or len({box.space for _, box in regions + dividers}) != 1:
        return ()
    partitions = {source_region(view, parent, node["ref"]) for node, _ in regions + dividers}
    if None in partitions or len(partitions) != 1:
        return ()
    ordered = sorted(regions, key=lambda item: item[1].x)
    pairs = list(zip(ordered, ordered[1:]))
    if any(left.right > right.x or left.vertical_overlap(right) < 0.75 for (_, left), (_, right) in pairs):
        return ()
    parent_box = geometry.boxes.get(parent["ref"])
    if parent_box is not None and any(not parent_box.contains(box) for _, box in regions + dividers):
        return ()
    for _, divider in dividers:
        if not any(
            left.right <= divider.x
            and divider.right <= right.x
            and divider.width <= min(left.width, right.width) * 0.08
            and divider.vertical_overlap(left) >= 0.75
            and divider.vertical_overlap(right) >= 0.75
            for (_, left), (_, right) in pairs
        ):
            return ()
    return tuple(ordered)
