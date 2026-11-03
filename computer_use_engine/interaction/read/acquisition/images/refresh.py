# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared policy and settling interval for bounded observation-image refresh."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from computer_use.interaction.read.acquisition.components.support import ObservationGap

IMAGE_REFRESH_DELAY_SECONDS = 0.08
IMAGE_REFRESH_MINIMUM_MS = 400
IMAGE_REFRESH_ATTEMPTS = 2


def can_refresh_image(
    *, attempt: int, scope_confirmed: bool, image, gaps: Sequence[ObservationGap], remaining: Callable[[], int]
) -> bool:
    """Grant the existing single refresh only to a missing stale image."""
    return (
        attempt == 0
        and scope_confirmed
        and image is None
        and any(gap.component == "image" and gap.code == "stale_target" for gap in gaps)
        and remaining() >= IMAGE_REFRESH_MINIMUM_MS
    )


async def settle_capture_geometry() -> None:
    """Share the existing wait without owning a retry or extending its deadline."""
    await asyncio.sleep(IMAGE_REFRESH_DELAY_SECONDS)
