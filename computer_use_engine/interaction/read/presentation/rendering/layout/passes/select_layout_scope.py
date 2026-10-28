# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select compact spatial packing, subtree lines, or diagnostic layout."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_layout_scope(frame: LayoutFrame, control: Control) -> str:
    """Keep subtree lines explicit and apply each full view's own layout policy."""
    if frame.context.get("subtreeRef"):
        return "unpacked"
    return "compact" if frame.context.get("compact") else "full"


stage = Pass.define(
    name="select_layout_scope",
    function=select_layout_scope,
    contract=Contract(LayoutFrame, str),
)
