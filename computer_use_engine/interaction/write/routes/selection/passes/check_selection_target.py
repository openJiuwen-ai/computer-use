# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check selection target."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.policy import validate_target
from computer_use.interaction.write.routes.selection.model import SelectionRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def check_selection_target(request: SelectionRequest) -> SelectionRequest:
    validate_target(request.facts, request.policy)
    return request


def execute(value: SelectionRequest, control: Control) -> SelectionRequest:
    """Validate the target identity, input scope, modal state, and desktop-mode bounds."""
    return check_selection_target(value)


stage = Pass.define(
    name="check_selection_target",
    function=execute,
    contract=Contract(SelectionRequest, SelectionRequest, failures=("target boundary",)),
)
