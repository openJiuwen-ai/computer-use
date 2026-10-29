# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan window menu parts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import (
    PURE,
    MenuFactPart,
    MenuPartsPlan,
    SelectedWindowMenus,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def plan_window_parts(value: SelectedWindowMenus, control: Control) -> MenuPartsPlan:
    """Distribute the node budget across selected menu sources and retain their discovery
    evidence.
    """
    discovered, selected = value.discovered, value.selected
    request, read, sources = discovered.lease.request, discovered.lease.read, discovered.sources
    quotient, extra = divmod(request.max_nodes, len(selected))
    parts = tuple(
        MenuFactPart(target, quotient + (index < extra), request.source_directory, "window")
        for index, target in enumerate(selected)
    )
    target = selected[0] if len(selected) == 1 and not sources.gaps else request.context
    return MenuPartsPlan(request, read, parts, target, sources.gaps)


stage = Pass.define(
    name="plan_window_menu_parts",
    function=plan_window_parts,
    contract=Contract(SelectedWindowMenus, MenuPartsPlan, PURE),
)
