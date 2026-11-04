# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select compact complete multiline values before text serialization."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.atoms.model import AtomFinalizeRequest
from computer_use.interaction.read.presentation.interface.atoms.finalization.multiline import select_multiline
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: AtomFinalizeRequest, control: Control) -> AtomFinalizeRequest:
    """Retain full original values while choosing their typed inline or block placement."""
    return select_multiline(request)


stage = Pass.define(
    name="choose_interface_multiline_content",
    function=execute,
    contract=Contract(
        AtomFinalizeRequest,
        AtomFinalizeRequest,
        {Effect.PURE},
        requires=("complete text evidence", "source-bound atom slots"),
        writes=("selected multiline field placement",),
    ),
)
