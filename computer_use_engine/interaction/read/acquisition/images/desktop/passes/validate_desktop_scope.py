# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate desktop scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.images.desktop.model import DesktopLease
from computer_use.native.contracts import Binding
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate_desktop_scope(value: DesktopLease, control: Control) -> DesktopLease:
    """Require the session-level binding used for native desktop observation."""
    target = value.read.target
    if target != Binding(target.session, None, None, None):
        raise ValueError("Desktop observation requires this session's read scope")
    return value


stage = Pass.define(
    name="validate_desktop_scope",
    function=validate_desktop_scope,
    contract=Contract(DesktopLease, DesktopLease, {Effect.PURE}),
)
