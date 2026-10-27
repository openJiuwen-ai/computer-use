"""Text-only packing after semantic rendering; references retain their source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.passes.group_empty_values import (
    execute as apply_group_empty_values,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_empty_values import (
    group_empty_values,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.join_container_chains import (
    execute as apply_join_container_chains,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.join_container_chains import (
    join_container_chains,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.join_text_parts import (
    execute as apply_join_text_parts,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.join_text_parts import (
    join_text_parts,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.select_layout_scope import (
    stage as select_layout_scope,
)
from computer_use.pipeline import Contract, Pass, Pipeline, Select, Sequence
from computer_use.protocol.primitives import required_item

if TYPE_CHECKING:
    from collections.abc import Sequence as SequenceItems
    from typing import Callable

    from computer_use.interaction.read.presentation.records import FormatField, NodeView
    from computer_use.pipeline.core import Step
    from computer_use.protocol.records import Record


STAGES = (group_empty_values, join_container_chains, join_text_parts)


def layout_pass(
    function: Callable[[list[str], SequenceItems[NodeView], Record, FormatField], list[str]],
) -> Pass[LayoutFrame, LayoutFrame]:
    """Wrap a text-layout transform as a pure Pass over the current layout frame.

    Args:
        function: Bound local handler; selecting it does not execute an unchosen branch.
    """
    handlers = {
        group_empty_values: apply_group_empty_values,
        join_container_chains: apply_join_container_chains,
        join_text_parts: apply_join_text_parts,
    }
    return Pass(
        function.__name__,
        required_item(handlers, function),
        Contract(
            LayoutFrame,
            LayoutFrame,
            requires=("rendered semantic lines",),
            writes=("packed lines", "layout diagnostics"),
        ),
    )


class LayoutPipeline(Pipeline["LayoutFrame", "LayoutFrame"]):
    """Apply text-layout transforms to rendered semantic lines.

    Choose natural compact/subtree lines or the full diagnostic packing sequence.
    Return the updated lines and optional per-stage counts in the frame used by final rendering.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[LayoutFrame, LayoutFrame]:
        return Select(
            name="LayoutPipeline",
            selector=select_layout_scope(),
            branches={
                "unpacked": Sequence(name="UnpackedLayout", steps=[layout_pass(group_empty_values)]),
                "full": Sequence(name="FullLayout", steps=[*(layout_pass(stage) for stage in STAGES)]),
            },
        )


def layout_lines(
    lines: list[str], roots: SequenceItems[NodeView], context: Record, format_field: FormatField
) -> tuple[list[str], list[Record]]:
    result = LayoutPipeline().run(LayoutFrame(lines, roots, context, format_field))
    return result.lines, list(result.records)
