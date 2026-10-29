# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain new scope for recollection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import PURE, ClassifiedAttempt
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ClassifiedAttempt, control: Control) -> ClassifiedAttempt:
    """Carry the scope-refresh decision to the gate that initializes the next attempt."""
    return value


stage = Pass.define(
    name="retain_new_scope_for_recollection",
    function=execute,
    contract=Contract(ClassifiedAttempt, ClassifiedAttempt, PURE),
)
