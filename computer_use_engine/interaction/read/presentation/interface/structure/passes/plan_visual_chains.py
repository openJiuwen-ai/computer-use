# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Choose inline parent-child edges already proven to describe one visual unit."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.plan_visual_chains import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Preserve each target while planning bounded, visually confirmed short chains."
    return apply(draft)


stage = Pass.define(
    name="plan_interface_visual_chains",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("proven visualChainChild edges and typed single-line atoms",),
        writes=("bounded chain layout on unchanged parent-child blocks",),
    ),
)
