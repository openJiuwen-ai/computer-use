# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Build initial blocks from semantic sources, preserving their original containment."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.algorithms.seed_regions import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Seed one exact-source block per projected object before making synthetic groups."
    return apply(draft)


stage = Pass.define(
    name="seed_interface_regions",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("original projected hierarchy",),
        writes=("source-backed region blocks",),
    ),
)
