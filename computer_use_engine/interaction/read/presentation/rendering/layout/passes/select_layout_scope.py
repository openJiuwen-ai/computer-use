"""Compact observations and explicit subtree reads retain natural lines."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_layout_scope(frame: LayoutFrame, control: Control) -> str:
    """Keep compact/subtree lines unpacked; reserve chain and text-part packing for
    full diagnostic rendering.
    """
    return "unpacked" if frame.context.get("subtreeRef") or frame.context.get("compact") else "full"


stage = Pass.define(
    name="select_layout_scope",
    function=select_layout_scope,
    contract=Contract(LayoutFrame, str),
)
