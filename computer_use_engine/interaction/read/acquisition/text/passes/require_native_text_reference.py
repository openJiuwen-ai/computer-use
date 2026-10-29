# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Require native text reference."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import PURE, CapturedTextPlan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def require_native_text_reference(plan: CapturedTextPlan, control: Control) -> CapturedTextPlan:
    """Require an immutable native capture reference before selecting driver text paging."""
    if plan.request.source.text_ref is None:
        raise ValueError("This observed field has no immutable full-text capture")
    return plan


stage = Pass.define(
    name="require_native_text_reference",
    function=require_native_text_reference,
    contract=Contract(CapturedTextPlan, CapturedTextPlan, PURE),
)
