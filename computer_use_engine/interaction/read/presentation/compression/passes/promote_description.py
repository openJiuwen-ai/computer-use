# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Promote description."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import REGIONS, ordinary_control

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def promote_description(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> None:
    """Promote a description to the name of an unnamed control, region, or heading."""
    matches_node = (
        True
        if (
            not node.get("name")
            and node.get("description")
            and (ordinary_control(node) or node["role"] in REGIONS or node["role"] == "heading")
        )
        else False
    )
    if matches_node:
        edit.adopt(node, [part for part in node["textSources"] if part["field"] == "description"], "name", " ")
        edit.suppress(node, "description")
