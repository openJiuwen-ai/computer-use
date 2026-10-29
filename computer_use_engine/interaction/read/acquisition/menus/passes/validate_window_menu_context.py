# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate window menu context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import PURE, MenuCollectionRequest
from computer_use.interaction.read.acquisition.menus.query import menu_failure
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate_window(request: MenuCollectionRequest, control: Control) -> MenuCollectionRequest:
    """Require an accessible fixed-window context with native instance, root, surface, and node
    identities.
    """
    context = request.context
    invalid_context = (
        True
        if (
            context is None
            or context.node is None
            or context.capture is not None
            or context.instance is None
            or context.root_window is None
            or context.surface is None
        )
        else False
    )
    if invalid_context:
        raise menu_failure("observation_incomplete", "Window-menu discovery needs an accessible, fixed working window.")
    return request


stage = Pass.define(
    name="validate_window_menu_context",
    function=validate_window,
    contract=Contract(MenuCollectionRequest, MenuCollectionRequest, PURE),
)
