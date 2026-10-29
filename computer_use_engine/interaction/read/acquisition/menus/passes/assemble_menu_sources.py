# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble menu sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import PURE, CollectedMenuParts
from computer_use.interaction.read.acquisition.menus.query import MenuRead
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assemble(value: CollectedMenuParts, control: Control) -> MenuRead:
    """Combine the planned menu scope, ordered snapshots, and retained discovery gaps."""
    return MenuRead(value.plan.target, value.parts, value.plan.discovery_gaps)


stage = Pass.define(
    name="assemble_menu_sources",
    function=assemble,
    contract=Contract(CollectedMenuParts, MenuRead, PURE),
)
