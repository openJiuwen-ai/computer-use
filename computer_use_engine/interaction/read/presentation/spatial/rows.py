# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Partition repeated visual rows while retaining exact member identities."""

from __future__ import annotations

from collections.abc import Sequence

from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, analyze_alignment, shared_anchors
from computer_use.interaction.read.presentation.spatial.geometry import Box, GeometryIndex
from computer_use.interaction.read.presentation.spatial.model import LayoutGroup
from computer_use.interaction.read.presentation.spatial.policy import (
    CONTAINED_CAPTIONS,
    complete_visible,
    logical_structure,
)


def repeated_rows(children: Sequence[NodeView], geometry: GeometryIndex) -> tuple[LayoutGroup, ...]:
    """Recognize repeated caption-and-control rows in the legacy semantic projection."""
    if any(node["role"] not in (CONTAINED_CAPTIONS | {"staticText", "heading", "image"}) for node in children):
        return ()
    return atomic_rows(children, geometry)


def atomic_rows(
    children: Sequence[NodeView], geometry: GeometryIndex, *, minimum_rows: int = 2
) -> tuple[LayoutGroup, ...]:
    """Recognize complete local caption-and-control rows, keeping unclassified leaves.

    Every member must have comparable bounds. Rows share a substantial vertical
    intersection and a common center band; repeated left, right or centre anchors
    establish their columns. Small status images can accompany text and buttons.
    A tall member spanning records or a missing local fact rejects the candidate.

    Args:
        children: Complete atomic units in the caller's already-approved scope.
        geometry: Comparable positive bounds with resolved coordinate spaces.
        minimum_rows: One for a single visual row, two for a repeated record layout.
    """
    if len(children) < minimum_rows * 2 or any(
        not complete_visible(node) or logical_structure(node) for node in children
    ):
        return ()
    boxes: dict[str, Box] = {}
    for node in children:
        box = geometry.boxes.get(node["ref"])
        if box is None or node["children"]:
            return ()
        boxes[node["ref"]] = box
    if len(boxes) != len(children) or len({box.space for box in boxes.values()}) != 1:
        return ()
    analysis = analyze_alignment(tuple(LayoutItem(ref, box) for ref, box in boxes.items()))
    bands = analysis.rows
    if (
        len(bands) < minimum_rows
        or any(len(row.members) < 2 for row in bands)
        or any(previous.box.bottom > following.box.y for previous, following in zip(bands, bands[1:]))
    ):
        return ()
    by_ref = {node["ref"]: node for node in children}
    rows = tuple(tuple(by_ref[ref] for ref in band.members) for band in bands)
    for row in rows:
        if not any(node["role"] in ("staticText", "heading") for node in row) or not any(
            node["role"] in CONTAINED_CAPTIONS for node in row
        ):
            return ()
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        return ()
    if len(rows) > 1 and any(
        not shared_anchors(analysis, tuple(node["ref"] for node in column)) for column in zip(*rows)
    ):
        return ()
    return tuple(LayoutGroup(kind="row", members=tuple(node["ref"] for node in row)) for row in rows)
