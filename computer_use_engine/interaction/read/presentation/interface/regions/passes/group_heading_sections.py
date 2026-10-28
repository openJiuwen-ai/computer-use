# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Use native headings to expose local sections without inventing actionable regions."

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.algorithms.group_heading_sections import apply
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    "Group heading-led sibling spans while keeping headings and content independently bound."
    return apply(draft)


stage = Pass.define(
    name="group_interface_heading_sections",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("native headings and local source order",),
        writes=("synthetic section organization",),
    ),
)
