# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wait capture geometry."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import QUERY, ClassifiedAttempt
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


async def image_wait(value: ClassifiedAttempt, control: Control) -> ClassifiedAttempt:
    """Wait 80 milliseconds before the selected image-geometry refresh."""
    await asyncio.sleep(0.08)
    return value


stage = Pass.define(
    name="wait_capture_geometry",
    function=image_wait,
    contract=Contract(ClassifiedAttempt, ClassifiedAttempt, QUERY),
)
