# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize prepared interface blocks and their typed atomic fields."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.text.formatting import format_interface_blocks
from computer_use.interaction.read.presentation.interface.text.model import FormattedInterface, InterfaceTextRequest
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: InterfaceTextRequest, control: Control) -> FormattedInterface:
    """Generate text from the frozen layout without invoking any layout selection pass."""
    return format_interface_blocks(request)


stage = Pass.define(
    name="serialize_interface_blocks",
    function=execute,
    contract=Contract(
        InterfaceTextRequest,
        FormattedInterface,
        {Effect.PURE},
        requires=("frozen block layout", "typed source-bound atom slots"),
        writes=("terminal block text", "independent incremental node text"),
    ),
)
