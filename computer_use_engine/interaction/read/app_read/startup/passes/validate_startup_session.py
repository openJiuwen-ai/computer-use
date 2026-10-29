# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate startup session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.startup.model import PURE, StartupLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate(value: StartupLease, control: Control) -> StartupLease:
    """Require the startup identity and open native lease to belong to the same driver session."""
    if value.read.target.session != value.state.session:
        from computer_use.interaction.read.app import failure

        raise failure("stale_target", "The launched application belongs to a previous driver session.")
    return value


stage = Pass.define(
    name="validate_startup_session",
    function=validate,
    contract=Contract(StartupLease, StartupLease, PURE),
)
