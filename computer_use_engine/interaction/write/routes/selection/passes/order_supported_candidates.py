# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Order supported candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.candidates import order_supported_candidates
from computer_use.interaction.write.routes.selection.model import CandidateSet
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: CandidateSet, control: Control) -> CandidateSet:
    """Order supported native candidates by the operation's delivery preferences."""
    return order_supported_candidates(value)


stage = Pass.define(
    name="order_supported_candidates",
    function=execute,
    contract=Contract(CandidateSet, CandidateSet),
)
