"""Promote description."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import REGIONS, ordinaryControl

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def promote_description(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> None:
    """Promote a description to the name of an unnamed control, region, or heading."""
    if (
        not node.get("name")
        and node.get("description")
        and (ordinaryControl(node) or node["role"] in REGIONS or node["role"] == "heading")
    ):
        edit.adopt(node, [part for part in node["textSources"] if part["field"] == "description"], "name", " ")
        edit.suppress(node, "description")
