# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan read components."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.model import ReadPlan
from computer_use.interaction.read.app_read.model import ApplicationSelection
from computer_use.interaction.read.limits import DEFAULT_WINDOW_MAX_NODES
from computer_use.interaction.read.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def plan(value: ApplicationSelection, control: Control) -> ReadPlan:
    """Build the window read plan from the selected instance and requested collection limits."""
    request = value.request
    return ReadPlan(
        cast("WorkingWindow", value.selection),
        cast("DirectoryFacts", value.directory),
        value.applications,
        value.launch,
        dict(
            fields=request.fields,
            max_nodes=DEFAULT_WINDOW_MAX_NODES if request.max_nodes is None else request.max_nodes,
            max_depth=request.max_depth,
            max_dimension=request.max_dimension,
            view=request.view,
            overview=request.overview,
        ),
    )


stage = Pass.define(
    name="plan_read_components",
    function=plan,
    contract=Contract(ApplicationSelection, ReadPlan, PURE),
)
if TYPE_CHECKING:
    from computer_use.native.scopes import WorkingWindow
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
