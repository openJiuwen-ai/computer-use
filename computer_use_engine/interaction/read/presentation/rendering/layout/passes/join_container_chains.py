from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.chains import compact_tree_chains
from computer_use.interaction.read.presentation.rendering.layout.packing import apply_layout

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import FormatField, NodeView
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def join_container_chains(
    lines: list[str], roots: Sequence[NodeView], context: Record, format_field: FormatField
) -> list[str]:
    return compact_tree_chains(lines, roots)


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Pack eligible parent/child chains and return the updated layout frame and optional stage counts."""
    return apply_layout(join_container_chains, frame)
