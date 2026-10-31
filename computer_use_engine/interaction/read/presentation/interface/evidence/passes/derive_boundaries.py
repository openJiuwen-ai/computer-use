# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Retain native scopes, independent operations and incomplete local content."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.evidence.algorithms.derive_boundaries import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Classify each source using observed facts, without inferring input from geometry."
    return apply(draft)


stage = Pass.define(
    name="derive_interface_boundaries",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("source fields and scopes",),
        writes=("local interface evidence",),
    ),
)
