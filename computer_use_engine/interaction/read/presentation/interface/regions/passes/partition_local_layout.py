# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Build neutral regions from substantial gaps in a local semantic scope."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.algorithms.partition_local_layout import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Partition unordered layout scopes while preserving every original block."
    return apply(draft)


stage = Pass.define(
    name="partition_interface_local_layout",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("local semantic scope and comparable geometry",),
        writes=("semantically constrained neutral column regions",),
    ),
)
