# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Choose compact runs of homogeneous independent leaf objects before text rendering."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.group_inline_members import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Share one prefix across short consecutive members while retaining all references."
    return apply(draft)


stage = Pass.define(
    name="group_interface_inline_members",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("same-scope consecutive leaf fields and prefix facts",),
        writes=("shared-prefix inline layout plan",),
    ),
)
