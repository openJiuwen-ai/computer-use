# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(options: RouteOptions, control: Control) -> str:
    """Choose the selected-command branch or continue to the next route preference."""
    return "selected" if options.selected is not None else "fallback"


stage = Pass.define(
    name="has_selected_route",
    function=execute,
    contract=Contract(RouteOptions, str),
)
