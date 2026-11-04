# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare exact field content and source disclosures before text generation."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.content.model import (
    FieldContentPlan,
    FieldContentRequest,
)
from computer_use.interaction.read.presentation.interface.composition.content.planning import plan_fields
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: FieldContentRequest, control: Control) -> FieldContentPlan:
    """Select target slots and explicit retained-member presentations from typed evidence."""
    return plan_fields(request)


stage = Pass.define(
    name="plan_interface_field_content",
    function=execute,
    contract=Contract(
        FieldContentRequest,
        FieldContentPlan,
        {Effect.PURE},
        requires=("source-backed field membership", "typed atoms", "captured detail route"),
        writes=("complete form-field content and exact source dispositions",),
    ),
)
