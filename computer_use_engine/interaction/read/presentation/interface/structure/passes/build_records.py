# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Promote proven visual rows to records while preserving every real member."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.build_records import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Build rectangular record blocks from previously verified local geometric groups."
    return apply(draft)


stage = Pass.define(
    name="build_interface_records",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("verified local visual rows and exact members",),
        writes=("record blocks and column layout plan",),
    ),
)
