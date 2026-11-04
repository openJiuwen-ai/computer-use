# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deliver desktop observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.images.desktop.model import DesktopLease
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def deliver(value: DesktopLease, control: Control) -> Record:
    """Return the completed desktop capture without starting any further query."""
    return cast("Record", value.result)


stage = Pass.define(
    name="deliver_desktop_observation",
    function=deliver,
    contract=Contract(DesktopLease, dict, {Effect.PURE}),
)
