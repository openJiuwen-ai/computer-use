# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify the selected layout after optional local recovery."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.inspection import layout_issues, validate_sources
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Require complete source coverage and a valid selected format for every block."""
    validate_sources(draft)
    issues = layout_issues(draft)
    if issues:
        raise ValueError("Invalid interface layout: " + issues[0].reason)
    return draft


stage = Pass.define(
    name="validate_interface_document",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete interface plan and original source index",),
        writes=(),
        failures=("presentation_contract",),
    ),
)
