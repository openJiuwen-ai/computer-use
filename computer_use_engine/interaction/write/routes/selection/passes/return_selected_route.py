# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.native.contracts import UnitCommand
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(options: RouteOptions, control: Control) -> UnitCommand:
    """Return the command chosen by the preceding route-selection branch."""
    return cast("UnitCommand", options.selected)


stage = Pass.define(
    name="return_selected_route",
    function=execute,
    contract=Contract(RouteOptions, UnitCommand),
)
