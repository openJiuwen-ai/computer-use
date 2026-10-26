"""Finalize sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import secondaryActions

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor
    from computer_use.interaction.read.presentation.records import NodeView


def finalize_sources(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Finalize source destinations and action-accounting evidence for the projection.

    Record primary actions omitted from secondary-action display, then resolve transitive fold
    and elision destinations for the source-provenance ledger.
    """

    def actions(node: NodeView) -> None:
        """Actions.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
        """
        secondary = {id(action) for action in secondaryActions(node)}
        omitted = [action["name"] for action in node.get("actions", ()) if id(action) not in secondary]
        if omitted:
            edit.event(sourceRef=node["ref"], kind="actions", actions=omitted)
        for child in node["children"]:
            actions(child)

    for root in view.roots:
        actions(root)
    edit.finalize_folds()


if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionView
