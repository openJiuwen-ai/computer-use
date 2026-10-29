# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable, Sequence

    from computer_use.interaction.read.presentation.records import FormatField, NodeView
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
    from computer_use.protocol.records import Record


def apply_layout(
    function: Callable[[list[str], Sequence[NodeView], Record, FormatField], list[str]],
    frame: LayoutFrame,
) -> LayoutFrame:
    """Apply a layout transform and carry its retained source records forward.

    Args:
        function: Bound local handler; selecting it does not execute an unchosen branch.
        frame: Rendered lines and their pure layout continuation; source bindings stay fixed.
    """
    output = function(frame.lines, frame.roots, frame.context, frame.format_field)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name=function.__name__, beforeLines=len(frame.lines), afterLines=len(output)),)
    return replace(frame, lines=output, records=records)
