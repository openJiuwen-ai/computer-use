# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wait startup catalogue."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.app_read.startup.model import QUERY, StartupDirectoryState
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


async def wait(state: StartupDirectoryState, control: Control) -> StartupDirectoryState:
    """Apply exponential startup-discovery backoff bounded by the original remaining deadline."""
    if state.attempt:
        await asyncio.sleep(min(0.1 * 2 ** (state.attempt - 1), cast("Callable[[], int]", control.remaining)() / 1000))
    return state


stage = Pass.define(
    name="wait_startup_catalogue",
    function=wait,
    contract=Contract(StartupDirectoryState, StartupDirectoryState, QUERY),
)
if TYPE_CHECKING:
    from typing import Callable
