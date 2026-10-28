# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Share exact prefixes across adjacent planned rows without removing row boundaries."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.share_inline_headers import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Wrap consecutive homogeneous inline rows in one source-backed prefix scope."
    return apply(draft)


stage = Pass.define(
    name="share_interface_inline_headers",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("consecutive typed inline layouts",),
        writes=("shared row header scopes",),
    ),
)
