"""Merge control labels."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.members import compactLabel, labelParts
from computer_use.interaction.read.presentation.compression.policy import ordinaryControl, sources
from computer_use.interaction.read.presentation.tree import valuePreviewLimit
from computer_use.protocol.primitives import utf16Length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def merge_control_labels(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> None:
    """Compose declared control labels into a concise control entry.

    Outside tables, derive a missing name from eligible label members or passive descendants.
    Compact the represented label/layout children using the owner relationship and provenance.
    """
    if scope.in_table or not ordinaryControl(node):
        return
    if not node.get("name"):
        if "control" in node:
            parts = []
            for member in node["control"]["members"]:
                source = state.nodes.get(member["ref"])
                if (
                    member["part"] == "label"
                    and source is not None
                    and utf16Length(source.get("value", "")) <= valuePreviewLimit
                ):
                    parts.extend(sources(source))
        else:
            parts = [part for child in node["children"] for part in (labelParts(state, edit, child, node) or [])]
        if parts:
            edit.adopt(node, parts, "name")
    edit.children(node, [c for child in node["children"] for c in compactLabel(state, edit, child, node)])
