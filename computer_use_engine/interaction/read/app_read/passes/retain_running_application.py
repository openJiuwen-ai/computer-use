# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain running application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.model import PURE, ApplicationSelection
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ApplicationSelection, control: Control) -> ApplicationSelection:
    """Carry the running selection directly to working-window resolution."""
    return value


stage = Pass.define(
    name="retain_running_application",
    function=execute,
    contract=Contract(ApplicationSelection, ApplicationSelection, PURE),
)
