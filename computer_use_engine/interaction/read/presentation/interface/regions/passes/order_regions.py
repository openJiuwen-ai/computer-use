# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Apply confirmed local reading constraints within existing semantic regions."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.algorithms.order_regions import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Order ordinary region children while retaining native collections and records."
    return apply(draft)


stage = Pass.define(
    name="order_interface_regions",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("native flow precedence and complete local geometry",),
        writes=("stable local region reading order",),
    ),
)
