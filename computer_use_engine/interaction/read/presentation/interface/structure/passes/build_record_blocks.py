# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Organize repeated native cards as complete records with shared role headers."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.build_record_blocks import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Choose record blocks for bounded repeated content that is too complex for columns."
    return apply(draft)


stage = Pass.define(
    name="build_interface_record_blocks",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete native groups", "repeated content with independent controls", "local comparable bounds"),
        writes=("complete record blocks and equal typed role prefixes",),
    ),
)
