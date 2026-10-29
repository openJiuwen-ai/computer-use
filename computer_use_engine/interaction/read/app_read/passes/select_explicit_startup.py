# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select explicit startup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.model import PURE, ApplicationSelection
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ApplicationSelection, control: Control) -> bool:
    """Select startup when application resolution produced no running surface directory."""
    return value.directory is None


stage = Pass.define(
    name="select_explicit_startup",
    function=execute,
    contract=Contract(ApplicationSelection, bool, PURE),
)
