# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Merge editors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.members import compact_input

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def merge_editors(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> None:
    """Compact declared text-input members beneath their owner using the observed editor relationship."""
    if node.get("control", {}).get("kind") == "textInput":
        edit.children(node, [c for child in node["children"] for c in compact_input(state, edit, child, node)])
