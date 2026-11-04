# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain source columns before generic layout wrappers can be flattened."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex
from computer_use.interaction.read.presentation.spatial.policy import walk
from computer_use.interaction.read.presentation.spatial.regions import parallel_regions
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def protect_spatial_regions(view: ProjectionView, edit: ProjectionEditor, geometry: GeometryIndex) -> None:
    """Keep distinct side-by-side regions as their existing native display nodes."""
    if not view.context.get("compact"):
        return
    for parent in walk(view.roots):
        regions = parallel_regions(view, parent, geometry)
        for index, (node, _) in enumerate(regions):
            if not node.get("preserveGroup"):
                edit.annotate(node, "preserveGroup", True)
                edit.event(
                    sourceRef=node["ref"],
                    kind="spatial-region",
                    geometrySources=tuple(child["ref"] for child, _ in regions),
                )
            if not node.get("name") and not node.get("description"):
                position = "left" if index == 0 else "right" if index == len(regions) - 1 else "middle"
                edit.annotate(node, "spatialPosition", position)
                edit.event(sourceRef=node["ref"], kind="spatial-position", position=position)


def execute(run: CompressionRun, control: Control) -> CompressionRun:
    run.edit.run(protect_spatial_regions, run.view, run.ir.source.geometry)
    return run


def stage() -> Pass[CompressionRun, CompressionRun]:
    return Pass(
        "protect_spatial_regions",
        execute,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=("complete scoped source regions", "comparable parallel bounds"),
            writes=("local region preservation annotation", "relative position annotation"),
            failures=("presentation_contract",),
        ),
    )
