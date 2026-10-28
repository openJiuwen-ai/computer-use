# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Text-only packing after semantic rendering; references retain their source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.passes.group_child_coverage import (
    stage as group_child_coverage,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_empty_values import (
    execute as apply_group_empty_values,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_empty_values import group_empty_values
from computer_use.interaction.read.presentation.rendering.layout.passes.group_field_descriptions import (
    stage as group_field_descriptions,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_field_prefixes import (
    stage as group_field_prefixes,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_identical_leaves import (
    stage as group_identical_leaves,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_identifier_prefix import (
    stage as group_identifier_prefix,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_input_fields import (
    stage as group_input_fields,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_leaf_headers import (
    stage as group_leaf_headers,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_multi_column_records import (
    stage as group_multi_column_records,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_role_labels import (
    stage as group_role_labels,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_row_records import (
    stage as group_row_records,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.group_sibling_actions import (
    stage as group_sibling_actions,
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
from computer_use.interaction.read.presentation.rendering.layout.passes.join_text_parts import join_text_parts
from computer_use.interaction.read.presentation.rendering.layout.passes.join_visual_chains import (
    execute as apply_join_visual_chains,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.join_visual_chains import join_visual_chains
from computer_use.interaction.read.presentation.rendering.layout.passes.publish_table_containment import (
    stage as publish_table_containment,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.select_layout_scope import (
    stage as select_layout_scope,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.share_exact_text_slices import (
    stage as share_exact_text_slices,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.share_local_field_suffixes import (
    stage as share_local_field_suffixes,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.share_table_entries import (
    stage as share_table_entries,
)
from computer_use.pipeline import Contract, Pass, Pipeline, Select, Sequence

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
        join_visual_chains: apply_join_visual_chains,
    }
    return Pass(
        function.__name__,
        handlers[function],
        Contract(
            LayoutFrame,
            LayoutFrame,
            requires=("rendered semantic lines",),
            writes=("packed lines", "layout diagnostics"),
        ),
    )


class LayoutPipeline(Pipeline["LayoutFrame", "LayoutFrame"]):
    """Apply text-layout transforms to rendered semantic lines.

    Choose spatially evidenced compact packing, natural subtree lines, or the
    full diagnostic packing sequence.
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
                "compact": Sequence(
                    name="CompactLayout",
                    steps=[
                        group_child_coverage(),
                        group_sibling_actions(),
                        layout_pass(group_empty_values),
                        layout_pass(join_visual_chains),
                        group_identical_leaves(),
                        group_leaf_headers(),
                        group_row_records(),
                        group_multi_column_records(),
                        share_exact_text_slices(),
                        group_identifier_prefix(),
                        group_field_prefixes(),
                        publish_table_containment(),
                        group_role_labels(),
                        group_input_fields(),
                        share_table_entries(),
                        share_local_field_suffixes(),
                        group_field_descriptions(),
                    ],
                ),
                "full": Sequence(name="FullLayout", steps=[*(layout_pass(stage) for stage in STAGES)]),
            },
        )


def layout_lines(
    lines: list[str], roots: SequenceItems[NodeView], context: Record, format_field: FormatField
) -> tuple[list[str], list[Record]]:
    result = LayoutPipeline().run(LayoutFrame(lines, roots, context, format_field))
    return result.lines, list(result.records)
