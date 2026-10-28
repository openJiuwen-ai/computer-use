# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Classify real controls and content without rewriting their capabilities or values."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.algorithms.classify_units import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Give projected input objects, controls and passive content their own block kinds."
    return apply(draft)


stage = Pass.define(
    name="classify_interface_units",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("observed role and original target",),
        writes=("reading unit kind",),
    ),
)
