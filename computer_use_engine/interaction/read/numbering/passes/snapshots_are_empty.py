# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Snapshots are empty."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.model import PURE, NumberingRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(request: NumberingRequest, control: Control) -> bool:
    """Select the empty-result branch when no snapshots require display numbering."""
    return not request.snapshots


stage = Pass.define(
    name="snapshots_are_empty",
    function=execute,
    contract=Contract(NumberingRequest, bool, PURE),
)
