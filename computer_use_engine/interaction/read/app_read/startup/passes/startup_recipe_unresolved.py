# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Startup recipe unresolved."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.startup.model import PURE, StartupLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: StartupLease, control: Control) -> bool:
    """Select installation association while startup still retains an unresolved installation
    identity.
    """
    return value.state.installation is not None


stage = Pass.define(
    name="startup_recipe_unresolved",
    function=execute,
    contract=Contract(StartupLease, bool, PURE),
)
