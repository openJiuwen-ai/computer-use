# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Attach immutable spatial evidence to the compression input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from computer_use.interaction.read.presentation.ir import ProjectionInput
from computer_use.interaction.read.presentation.model import BranchFacts, PURE
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex, collect_geometry
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def _outside_window_fields(source: ProjectionInput, branch: BranchFacts, geometry: GeometryIndex) -> frozenset[str]:
    """Compare reported bounds of direct text fields within this captured window branch."""
    request = branch.request
    snapshot = request.snapshot
    native = snapshot.native
    root = snapshot.refs.get(source.root)
    frame = geometry.boxes.get(source.root)
    if not (request.compact and request.public_api and request.share_descriptions):
        return frozenset()
    if snapshot.scope_kind != "window" or native.backend != "macos":
        return frozenset()
    if root is None or native.roots != (root,) or frame is None:
        return frozenset()
    window = native.nodes[root]
    children = window.relations.get("children")
    if window.role != "window" or children is None or children.evidence == "candidate":
        return frozenset()

    # An observed child slot remains evidence when the rest of the catalogue is partial.
    direct = frozenset(children.targets)
    outside = set()
    for ref in source.order:
        token = snapshot.refs.get(ref)
        box = geometry.boxes.get(ref)
        if token not in direct or box is None or not box.comparable(frame):
            continue
        node = native.nodes[token]
        if node.role != "text_field" or node.states.enabled is not True:
            continue
        parent, owner = node.relations.get("parent"), node.relations.get("window")
        if parent is None or parent.evidence == "candidate":
            continue
        if parent.targets != (root,) or parent.total != 1:
            continue
        if owner is not None and any(target != root for target in owner.targets):
            continue
        if max(box.x, frame.x) > min(box.right, frame.right) or max(box.y, frame.y) > min(box.bottom, frame.bottom):
            outside.add(ref)
    return frozenset(outside)


def create(*, branch: BranchFacts) -> Callable[[ProjectionInput, Control], ProjectionInput]:
    def execute(source: ProjectionInput, control: Control) -> ProjectionInput:
        """Retain comparable bounds and any local geometry cue used by the display."""
        geometry = collect_geometry(branch.request.snapshot)
        outside = _outside_window_fields(source, branch, geometry)
        return replace(source, geometry=geometry, outside_window_fields=outside)

    return execute


stage = Pass.factory(
    name="collect_geometry",
    factory=create,
    contract=Contract(
        ProjectionInput,
        ProjectionInput,
        PURE,
        requires=("captured geometry and coordinate spaces",),
        writes=("immutable geometry index", "same-window bounds cue"),
    ),
)
