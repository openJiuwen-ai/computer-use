# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Attach immutable spatial evidence to the compression input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from computer_use.interaction.read.presentation.ir import ProjectionInput
from computer_use.interaction.read.presentation.model import BranchFacts, PURE
from computer_use.interaction.read.presentation.spatial.geometry import collect_geometry
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def create(*, branch: BranchFacts) -> Callable[[ProjectionInput, Control], ProjectionInput]:
    def execute(source: ProjectionInput, control: Control) -> ProjectionInput:
        """Retain comparable bounds separately from the node fields used by diff."""
        return replace(source, geometry=collect_geometry(branch.request.snapshot))

    return execute


stage = Pass.factory(
    name="collect_geometry",
    factory=create,
    contract=Contract(
        ProjectionInput,
        ProjectionInput,
        PURE,
        requires=("captured geometry and coordinate spaces",),
        writes=("immutable geometry index",),
    ),
)
