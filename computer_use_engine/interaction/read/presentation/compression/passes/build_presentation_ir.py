# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build presentation ir."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.ir import (
    PresentationIR,
    ProjectionEditor,
    ProjectionInput,
    ProjectionView,
)
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def build_presentation_ir(source: ProjectionInput, control: Control) -> CompressionRun:
    """Create the editable projection and annotate declared editor members with their owner
    reference.
    """
    ir = PresentationIR.build(source)
    view, edit = ProjectionView(ir), ProjectionEditor(ir)
    for node in view.nodes.values():
        if node.get("control", {}).get("kind") == "textInput":
            for member in node["control"]["members"]:
                if member["part"] == "editor" and member["ref"] in view.nodes:
                    edit.annotate(view.nodes[member["ref"]], "editorOwnerRef", node["ref"])
    return CompressionRun(ir, view, edit)


stage = Pass.define(
    name="build_presentation_ir",
    function=build_presentation_ir,
    contract=Contract(ProjectionInput, CompressionRun, {Effect.PURE}, writes=("private PresentationIR",)),
)
