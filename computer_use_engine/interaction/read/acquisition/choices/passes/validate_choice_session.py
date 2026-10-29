# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate choice session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.choices.model import WindowChoiceLease
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate(value: WindowChoiceLease, control: Control) -> WindowChoiceLease:
    """Require the window-choice directory and open read lease to share a driver session."""
    if value.read.target.session != value.draft.request.directory["session"]:
        from computer_use.interaction.read.app import failure

        raise failure("stale_target", "The application belongs to a previous driver session.")
    return value


stage = Pass.define(
    name="validate_choice_session",
    function=validate,
    contract=Contract(WindowChoiceLease, WindowChoiceLease, {Effect.PURE}),
)
