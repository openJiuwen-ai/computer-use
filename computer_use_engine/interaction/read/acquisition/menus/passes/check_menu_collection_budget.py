# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check menu collection budget."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.menus.model import QUERY, CollectedMenuParts
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def check_budget(value: CollectedMenuParts, control: Control) -> CollectedMenuParts:
    """Check the enclosing call deadline before returning the collected menu snapshots."""
    cast("Callable[[], int]", control.remaining)()
    return value


stage = Pass.define(
    name="check_menu_collection_budget",
    function=check_budget,
    contract=Contract(CollectedMenuParts, CollectedMenuParts, QUERY),
)
if TYPE_CHECKING:
    from typing import Callable
