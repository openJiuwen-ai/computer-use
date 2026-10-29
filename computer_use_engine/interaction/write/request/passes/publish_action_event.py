# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish action event."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.session.events import action_completed

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def publish(receipt: Record, control: Control) -> Record:
    """Publish the completed action receipt to session observers."""
    action_completed(receipt)
    return receipt


stage = Pass.define(
    name="publish_action_event",
    function=publish,
    contract=Contract(dict, dict, {Effect.RESOURCE}),
)
