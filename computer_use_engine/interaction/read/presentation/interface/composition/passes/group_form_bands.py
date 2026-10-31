# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Organize repeated form rows before choosing any caption compression."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.algorithms.group_form_bands import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Keep complete original members in labelled visual fields with no synthetic target."
    return apply(draft)


stage = Pass.define(
    name="group_interface_form_bands",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("repeated label column with same-scope complete member bounds",),
        writes=("visual field blocks preserving original member facts and identities",),
    ),
)
