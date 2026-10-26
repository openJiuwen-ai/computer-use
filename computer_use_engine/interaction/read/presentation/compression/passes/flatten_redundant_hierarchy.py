"""Flatten redundant hierarchy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.boundaries import anonymous_layout
from computer_use.interaction.read.presentation.compression.policy import sameBlocking

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def flatten_redundant_hierarchy(
    state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope
) -> list[ViewNode]:
    """Splice equivalent anonymous child groups into a compact parent group.

    For an eligible unfocused group outside tables, replace anonymous child groups sharing its
    blocking context with their children. Record each splice and return the retained parent.
    """
    if not state.context.get("compact") or scope.in_table or node["role"] != "group":
        return [node]
    if state.keep(node) or node["ref"] == state.context.get("focusedRef") or not anonymous_layout(node):
        return [node]
    children = []
    for child in node["children"]:
        if (
            child["role"] == node["role"]
            and anonymous_layout(child)
            and sameBlocking(node, child)
            and not state.keep(child)
            and child["ref"] != state.context.get("focusedRef")
        ):
            edit.elide(child, "splice", child["children"], target=node)
            children.extend(child["children"])
        else:
            children.append(child)
    edit.children(node, children)
    return [node]
