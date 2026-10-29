# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prune empty rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import independent
from computer_use.interaction.read.presentation.tree import disclosure

if TYPE_CHECKING:
    from typing import Any

    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def prune_empty_rows(
    state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope
) -> list[Any] | list[ViewNode]:
    """Omit an empty passive row outside table and focused/requested scopes, recording its elision."""

    def eligible_empty_row() -> bool:
        """Require each original projection condition in its original order."""
        if not (not scope.in_table and not state.keep(node) and node["role"] == "row"):
            return False
        if not (not node.get("name") and not node.get("description") and "value" not in node):
            return False
        if not (
            not node["children"]
            and not any(node.get(k) for k in ("selected", "selectable", "focused", "pressable"))
            and node.get("enabled") is not False
        ):
            return False
        if not (disclosure(node) is None and not independent(node) and node["ref"] != state.context.get("focusedRef")):
            return False
        if not (
            not any(
                ref == state.context.get("focusedRef") or state.native_nodes.get(ref, {}).get("focused")
                for ref in node["sourceRefs"]
            )
        ):
            return False
        return True

    if eligible_empty_row():
        edit.elide(node, "empty")
        return []
    return [node]
