# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wait capture geometry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import QUERY, ClassifiedAttempt
from computer_use.interaction.read.acquisition.images.refresh import settle_capture_geometry
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


async def image_wait(value: ClassifiedAttempt, control: Control) -> ClassifiedAttempt:
    """Wait 80 milliseconds before the selected image-geometry refresh."""
    await settle_capture_geometry()
    return value


stage = Pass.define(
    name="wait_capture_geometry",
    function=image_wait,
    contract=Contract(ClassifiedAttempt, ClassifiedAttempt, QUERY),
)
