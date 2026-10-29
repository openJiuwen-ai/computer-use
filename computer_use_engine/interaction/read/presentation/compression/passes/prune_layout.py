# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prune layout."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.boundaries import anonymous_layout
from computer_use.interaction.read.presentation.compression.policy import REGIONS, WRAPPERS, layout

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def prune_layout(
    state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope
) -> list[ViewNode] | tuple[ViewNode, ...]:
    """Replace eligible transparent layout nodes with their ordered children.

    Apply the view's compact or ordinary layout policy, retaining requested and focused nodes
    and table-cell boundaries. Record the splice before returning replacement children.
    """
    parent = scope.parent
    sole_wrapper = (
        parent is not None
        and len(parent["children"]) == 1
        and parent["role"] in REGIONS
        and (parent["role"] not in WRAPPERS or parent.get("name") or parent.get("description"))
    )
    removable = anonymous_layout(node) if state.context.get("compact") else layout(node)
    raw_layout = (
        node.get("_factBacked")
        and node["role"] == "group"
        and node.get("isControlElement") is False
        and node.get("isContentElement") is False
    )
    matches_removable = (
        True
        if (
            not state.keep(node)
            and node["ref"] != state.context.get("focusedRef")
            and removable
            and (not scope.in_table or node["role"] not in ("cell", "column"))
            and (len(node["children"]) <= 1 or sole_wrapper or raw_layout)
        )
        else False
    )
    if matches_removable:
        edit.elide(node, "splice", node["children"])
        return node["children"]
    return [node]
