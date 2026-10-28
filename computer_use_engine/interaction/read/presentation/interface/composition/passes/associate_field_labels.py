# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Compose complete source-backed form units without merging their input targets."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.algorithms.associate_field_labels import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Arrange unique labels and native descriptions with their existing input field."
    return apply(draft)


stage = Pass.define(
    name="associate_interface_field_labels",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("unique native or prepared visual membership",),
        writes=("source-bound form fields and descriptions",),
    ),
)
