# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish shared row, column and association evidence without choosing a layout."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.geometric.model import (
    GeometricLink,
    GeometricRelationGraph,
    GeometricRelationScope,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft


def capture_geometric_relations(draft: InterfaceDraft) -> InterfaceDraft:
    """Index local alternatives while retaining native constraints and identities.

    Args:
        draft: Whole-unit alignment plans and their unchanged source block graph.

    Returns:
        A draft with a frozen relationship graph. A row or column link is a layout
        candidate; it does not assert a label, group role or input capability.
    """
    scopes = {}
    for key, plan in draft.geometric_alignment.items():
        rows = tuple(tuple(row.members) for row in plan.alignment.rows)
        anchors = tuple(
            dict.fromkeys(tuple(anchor.members) for anchor in plan.alignment.anchors if len(anchor.members) > 1)
        )
        links = (
            *(GeometricLink(group, "association", "required") for group in plan.keep_together),
            *(GeometricLink(row, "row", "candidate") for row in rows if len(row) > 1),
            *(GeometricLink(anchor, "column", "candidate") for anchor in anchors),
        )
        scopes[key] = GeometricRelationScope(
            plan.source_order, plan.before, plan.keep_together, plan.segments, rows, anchors, links
        )
    return replace(draft, geometric_relations=GeometricRelationGraph(scopes))
