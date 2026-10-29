# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""No snapshot numbers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.model import PURE, NumberingRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.pipeline.core import Control


def execute(request: NumberingRequest, control: Control) -> tuple[SemanticSnapshot, ...]:
    """Return the empty numbered-snapshot sequence."""
    return ()


stage = Pass.define(
    name="no_snapshot_numbers",
    function=execute,
    contract=Contract(NumberingRequest, tuple, PURE),
)
