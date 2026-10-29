# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect pixels."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.helpers import image_notes
from computer_use.interaction.read.acquisition.components.model import ComponentLease
from computer_use.interaction.read.acquisition.components.support import (
    ObservationGap,
    collect_component,
)
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[ComponentLease, Control], Awaitable[ComponentLease]]:
    async def collect(value: ComponentLease, control: Control) -> ComponentLease:
        """Attach requested window pixels or supported capture gaps to the component lease.

        Report missing native-capture support as an image gap. Otherwise collect within half the
        remaining budget and retain capture-state notes with the image result.
        """
        request, parts = value.request, value.parts
        if not request.include_image:
            return value
        if "nativeCapture" not in services.port.capabilities:
            gap = ObservationGap(
                "image",
                "mode_unsupported",
                "This driver does not declare window capture.",
                "use a driver with nativeCapture support",
            )
            return replace(value, parts=replace(parts, gaps=parts.gaps + (gap,)))
        image, gap = await collect_component(
            services.port,
            "image",
            lambda: services.images.collect(
                value.read.bounded(max(1, control.remaining() // 2)), max_dimension=request.limits["max_dimension"]
            ),
        )
        return replace(
            value, parts=replace(parts, image=image, gaps=parts.gaps + ((gap,) if gap else ()) + image_notes(image))
        )

    return collect


QUERY = {Effect.QUERY}


stage = Pass.factory(
    name="collect_pixels",
    factory=create,
    contract=Contract(ComponentLease, ComponentLease, QUERY),
)
