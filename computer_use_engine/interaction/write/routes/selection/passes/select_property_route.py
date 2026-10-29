# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select property route."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.model import RouteOptions
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_property_route(options: RouteOptions) -> RouteOptions:
    if options.property_replacements:
        return replace(options, selected=options.property_replacements[0])
    return options


def execute(value: RouteOptions, control: Control) -> RouteOptions:
    """Choose the first eligible value-replacement command, if one remains."""
    return select_property_route(value)


stage = Pass.define(
    name="select_property_route",
    function=execute,
    contract=Contract(RouteOptions, RouteOptions),
)
