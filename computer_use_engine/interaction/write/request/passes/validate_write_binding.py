# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate write binding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.requests import ExecuteAction
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control


def create(*, validate_element: Callable[[Binding], None]) -> Callable[[ExecuteAction, Control], ExecuteAction]:

    def validate(request: ExecuteAction, control: Control) -> ExecuteAction:
        """Check that the action target belongs to the active controller session."""
        validate_element(request.target)
        return request

    return validate


stage = Pass.factory(
    name="validate_write_binding",
    factory=create,
    contract=Contract(ExecuteAction, ExecuteAction),
)
