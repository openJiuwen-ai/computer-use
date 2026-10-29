# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parse desktop request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def parse_desktop_request(request: DesktopReadRequest, control: Control) -> DesktopReadRequest:
    """Require a maximum desktop image dimension between 1 and 4096 pixels."""
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(request.max_dimension) is not int or not 1 <= request.max_dimension <= 4096:
        raise ValueError("Image dimension budget must be an integer in 1..4096")
    return request


stage = Pass.define(
    name="parse_desktop_request",
    function=parse_desktop_request,
    contract=Contract(DesktopReadRequest, DesktopReadRequest, {Effect.PURE}),
)
