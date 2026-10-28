# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Index a projection without changing its native or displayed source fields."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.evidence.algorithms.index_sources import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceRequest, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: InterfaceRequest, control: Control) -> InterfaceDraft:
    "Detach the projected forest and replace child dictionaries with exact references."
    return apply(request)


stage = Pass.define(
    name="index_interface_sources",
    function=execute,
    contract=Contract(
        InterfaceRequest,
        InterfaceDraft,
        {Effect.PURE},
        requires=("source-preserved projection",),
        writes=("immutable interface source index",),
    ),
)
