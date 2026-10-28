# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Associate each complete radio question with all of its independently bound options."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.algorithms.option_groups import group_radio_options
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Keep proved option questions local while preserving each original input target."""
    return group_radio_options(draft)


stage = Pass.define(
    name="GeometricOptionGroupPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete local radio options and source-backed question evidence",),
        writes=("source-preserving question and option display membership",),
    ),
)
