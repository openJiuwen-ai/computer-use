# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture precise generated child-coverage hints for optional text sharing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import ChildCoverageHint

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionInput
    from computer_use.interaction.read.presentation.records import NodeView


def capture_child_hint(
    node: NodeView,
    source: ProjectionInput,
    line_index: int,
    text: str,
) -> ChildCoverageHint | None:
    """Record the exact empty initial-page case without inferring a continuation cursor.

    The projected and original nodes must report the same zero range and positive
    total. Distinct partial reasons remain separate, and any omissions or cursor
    keep their existing per-element presentation.
    """
    original = source.native_nodes.get(node["ref"])
    if original is None:
        return None
    for candidate in (node, original):
        if (
            type(candidate.get("childrenStart")) is not int
            or candidate["childrenStart"] != 0
            or type(candidate.get("childrenEnd")) is not int
            or candidate["childrenEnd"] != 0
            or type(candidate.get("childCount")) is not int
            or candidate["childCount"] <= 0
            or candidate.get("children")
            or "nextChildOffset" in candidate
            or candidate.get("childOmissions")
            or candidate.get("childrenRead", 0) != 0
            or not isinstance(candidate.get("partialReason"), str)
            or not candidate["partialReason"]
        ):
            return None
    if any(node.get(key) != original.get(key) for key in ("childCount", "partialReason")):
        return None
    scope = source.scopes.get(node["ref"])
    if scope is None:
        return None
    current: str | None = node["ref"]
    while current is not None:
        ancestor = source.native_nodes.get(current)
        if ancestor is not None and ancestor["role"] in ("window", "sheet", "menuBar", "menu"):
            scope = current
            break
        current = source.parents.get(current)
    return ChildCoverageHint(node["ref"], node["childCount"], node["partialReason"], scope, line_index, text)
