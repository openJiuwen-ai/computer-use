# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Add short position hints only to otherwise ambiguous parallel regions."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex
from computer_use.interaction.read.presentation.spatial.policy import walk
from computer_use.interaction.read.presentation.spatial.regions import parallel_regions
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def describe_spatial_regions(view: ProjectionView, edit: ProjectionEditor, geometry: GeometryIndex) -> None:
    """Distinguish unnamed side-by-side source containers without inventing their purpose."""
    for parent in walk(view.roots):
        ordered = parallel_regions(view, parent, geometry)
        for index, (node, _) in enumerate(ordered):
            if node.get("name") or node.get("description"):
                continue
            position = "left" if index == 0 else "right" if index == len(ordered) - 1 else "middle"
            edit.annotate(node, "spatialPosition", position)
            edit.event(sourceRef=node["ref"], kind="spatial-position", position=position)


def execute(run: CompressionRun, control: Control) -> CompressionRun:
    run.edit.run(describe_spatial_regions, run.view, run.ir.source.geometry)
    return run


def stage() -> Pass[CompressionRun, CompressionRun]:
    return Pass(
        "describe_spatial_regions",
        execute,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=("parallel source regions", "comparable bounds"),
            writes=("relative position annotation",),
            failures=("presentation_contract",),
        ),
    )
