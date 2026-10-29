# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply platform preferences."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.candidates import apply_platform_preferences
from computer_use.interaction.write.routes.selection.model import CandidateSet
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: CandidateSet, control: Control) -> CandidateSet:
    """Filter candidate units according to platform, desktop mode, and input semantics."""
    return apply_platform_preferences(value)


stage = Pass.define(
    name="apply_platform_preferences",
    function=execute,
    contract=Contract(CandidateSet, CandidateSet),
)
