# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Plan short control runs and explicit wrapper paths inside complete record blocks."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.plan_record_members import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Expose record fields as original ancestor paths and keep nearby actions compact."
    return apply(draft)


stage = Pass.define(
    name="plan_interface_record_members",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete record membership", "exact native single-child edges", "typed fields and capabilities"),
        writes=("record ancestor paths and independent compact control runs",),
    ),
)
