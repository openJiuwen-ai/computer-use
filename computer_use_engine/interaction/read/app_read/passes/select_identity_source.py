# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select identity source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app import StartingApplication, WorkingWindow
from computer_use.interaction.read.app_read.model import PURE, ReadCall
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def source(call: ReadCall, control: Control) -> str:
    """Choose fixed-window, starting-application, or directory-based scope resolution."""
    if isinstance(call.request.selector, WorkingWindow):
        return "fixed"
    if isinstance(call.request.selector, StartingApplication):
        return "starting"
    return "directory"


stage = Pass.define(
    name="select_identity_source",
    function=source,
    contract=Contract(ReadCall, str, PURE),
)
