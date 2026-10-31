# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mark tightly nested source controls for reversible terminal line packing."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView
from computer_use.interaction.read.presentation.spatial.geometry import Box, GeometryIndex
from computer_use.interaction.read.presentation.spatial.policy import (
    CONTAINED_CAPTIONS,
    complete_visible,
    visible_source_path,
    walk,
)
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


_CHILD_ROLES = CONTAINED_CAPTIONS | frozenset(("group",))


def _tight_containment(parent: Box, child: Box) -> bool:
    """Recognize one visual footprint while keeping larger surrounding regions."""
    if not parent.contains(child):
        return False
    if child.width < parent.width * 0.8 or child.height < parent.height * 0.8:
        return False
    allowance = min(child.width, child.height) / 4
    return (
        max(child.x - parent.x, child.y - parent.y, parent.right - child.right, parent.bottom - child.bottom)
        <= allowance
    )


def mark_visual_chains(view: ProjectionView, edit: ProjectionEditor, geometry: GeometryIndex) -> None:
    """Mark scoped single-child wrappers whose boxes describe the same visual unit.

    Keep both elements, every field and each action on its existing source. The
    marker authorizes compact line layout only; it leaves hierarchy, roles and
    capabilities unchanged. A wide region around one small control stays separate.
    """
    for parent in walk(view.roots):
        if (
            parent["role"] != "group"
            or len(parent["children"]) != 1
            or len(view.native_nodes[parent["ref"]]["children"]) != 1
            or parent.get("layoutGroups")
            or parent.get("spatialPosition")
            or parent.get("blockedBy")
            or parent.get("modal")
            or not complete_visible(parent)
            or not visible_source_path(view, parent)
        ):
            continue
        child = parent["children"][0]
        if (
            child["role"] not in _CHILD_ROLES
            or child.get("blockedBy")
            or child.get("modal")
            or child.get("layoutGroups")
            or child.get("spatialPosition")
            or not complete_visible(child)
            or not visible_source_path(view, child)
            or view.scopes[parent["ref"]] != view.scopes[child["ref"]]
        ):
            continue
        outer, inner = geometry.boxes.get(parent["ref"]), geometry.boxes.get(child["ref"])
        if outer is None or inner is None or not _tight_containment(outer, inner):
            continue
        edit.annotate(parent, "visualChainChild", child["ref"])
        edit.event(sourceRef=parent["ref"], kind="visual-chain", childRef=child["ref"])


def execute(run: CompressionRun, control: Control) -> CompressionRun:
    run.edit.run(mark_visual_chains, run.view, run.ir.source.geometry)
    return run


def stage() -> Pass[CompressionRun, CompressionRun]:
    return Pass(
        "mark_visual_chains",
        execute,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=("complete scoped single-child wrappers", "comparable tight bounds"),
            writes=("visual chain annotation",),
            failures=("presentation_contract",),
        ),
    )
