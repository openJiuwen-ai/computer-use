# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep each repeated visual record's caption, state and controls together."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.compression.policy import same_blocking
from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex
from computer_use.interaction.read.presentation.spatial.policy import (
    available_region,
    logical_source_scope,
    source_region,
    visible_source_path,
    walk,
)
from computer_use.interaction.read.presentation.spatial.rows import repeated_rows
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def group_spatial_rows(view: ProjectionView, edit: ProjectionEditor, geometry: GeometryIndex) -> None:
    """Annotate complete local rows without creating native nodes or input bindings."""
    for parent in walk(view.roots):
        if not available_region(parent) or not visible_source_path(view, parent) or logical_source_scope(view, parent):
            continue
        children = list(parent["children"])
        if any(
            view.scopes[child["ref"]] != view.scopes[parent["ref"]] or not same_blocking(parent, child)
            for child in children
        ):
            continue
        groups = repeated_rows(children, geometry)
        if not groups:
            continue
        partitions = {child["ref"]: source_region(view, parent, child["ref"]) for child in children}
        if None in partitions.values() or any(
            len({partitions[ref] for ref in group["members"]}) != 1 for group in groups
        ):
            continue
        edit.annotate(parent, "layoutGroups", groups)
        edit.children(parent, [view.nodes[ref] for group in groups for ref in group["members"]])
        for group in groups:
            captions = tuple(ref for ref in group["members"] if view.nodes[ref]["role"] in ("staticText", "heading"))
            for ref in group["members"]:
                node = view.nodes[ref]
                edit.annotate(node, "_contextRefs", tuple(dict.fromkeys((*node.get("_contextRefs", ()), *captions))))
        edit.event(sourceRef=parent["ref"], kind="spatial-rows", groups=groups)


def execute(run: CompressionRun, control: Control) -> CompressionRun:
    run.edit.run(group_spatial_rows, run.view, run.ir.source.geometry)
    return run


def stage() -> Pass[CompressionRun, CompressionRun]:
    return Pass(
        "group_spatial_rows",
        execute,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=("comparable bounds", "complete local display children"),
            writes=("layout groups", "ordered display children", "diff context"),
            failures=("presentation_contract",),
        ),
    )
