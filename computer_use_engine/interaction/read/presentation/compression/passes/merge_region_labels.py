"""Merge region labels."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.captions import passive_label
from computer_use.interaction.read.presentation.compression.policy import REGIONS, represented, sources

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def merge_region_labels(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> list[ViewNode]:
    """Promote a region's unique leading heading and fold represented passive captions.

    Outside tables, use the leading heading as the unnamed region's caption when unambiguous.
    Update its children to retain independent content and record folded caption destinations.
    """
    if node["role"] not in REGIONS or scope.in_table:
        return [node]
    headings = [child for child in node["children"] if child["role"] == "heading" and child.get("name")]
    if not node.get("name") and len(headings) == 1 and node["children"][0] is headings[0]:
        edit.adopt(node, sources(headings[0]), "name")
    retained = []
    for child in node["children"]:
        if passive_label(state, edit, child, node) and not child["children"] and represented(child, node):
            edit.fold(child, node, "label")
        else:
            retained.append(child)
    edit.children(node, retained)
    return [node]
