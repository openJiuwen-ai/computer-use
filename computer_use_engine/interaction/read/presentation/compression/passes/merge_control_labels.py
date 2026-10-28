"""Merge control labels."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.members import compact_label, label_parts
from computer_use.interaction.read.presentation.compression.policy import ordinary_control, sources
from computer_use.interaction.read.presentation.tree import VALUE_PREVIEW_LIMIT
from computer_use.protocol.primitives import utf16_length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def merge_control_labels(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> None:
    """Compose declared control labels into a concise control entry.

    Outside tables, derive a missing name from eligible label members or passive descendants.
    Compact the represented label/layout children using the owner relationship and provenance.
    """
    if scope.in_table or not ordinary_control(node):
        return
    if not node.get("name"):
        if "control" in node:
            parts = []
            for member in node["control"]["members"]:
                source = state.nodes.get(member["ref"])
                if (
                    member["part"] == "label"
                    and source is not None
                    and utf16_length(source.get("value", "")) <= VALUE_PREVIEW_LIMIT
                ):
                    parts.extend(sources(source))
        else:
            parts = [part for child in node["children"] for part in (label_parts(state, edit, child, node) or [])]
        if parts:
            edit.adopt(node, parts, "name")
    edit.children(node, [c for child in node["children"] for c in compact_label(state, edit, child, node)])
