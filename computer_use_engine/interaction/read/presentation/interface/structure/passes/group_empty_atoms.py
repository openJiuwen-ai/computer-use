# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Represent repeated empty controls by a shared prefix and exact reference set."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.group_empty_atoms import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Compact consecutive, identical empty siblings without implying a visual row."
    return apply(draft)


stage = Pass.define(
    name="group_interface_empty_atoms",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete empty atomic slots and identical native sibling prefixes",),
        writes=("reference-set list layout with independent targets",),
    ),
)
