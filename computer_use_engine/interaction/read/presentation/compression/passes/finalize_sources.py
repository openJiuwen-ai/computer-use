# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Finalize sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor
    from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.compression.descriptions import folded_source_roles
from computer_use.interaction.read.presentation.compression.policy import secondary_actions


def finalize_sources(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Finalize source destinations and action-accounting evidence for the projection.

    Record primary actions omitted from secondary-action display, then resolve transitive fold
    and elision destinations for the source-provenance ledger. Retain exact role fields of
    sources folded after their empty descriptions obtained a public shared destination.
    """

    def actions(node: NodeView) -> None:
        """Actions.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
        """
        secondary = {id(action) for action in secondary_actions(node)}
        omitted = [action["name"] for action in node.get("actions", ()) if id(action) not in secondary]
        if omitted:
            edit.event(sourceRef=node["ref"], kind="actions", actions=omitted)
        for child in node["children"]:
            actions(child)

    for root in view.roots:
        actions(root)
    edit.finalize_folds()
    for group in folded_source_roles(view):
        edit.declare_source_roles(group)


if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionView
