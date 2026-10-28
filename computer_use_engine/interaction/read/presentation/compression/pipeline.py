# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One source-preserving compression traversal with explicit pure rules.

The traversal owns ordering. Rules receive a read-only view plus a narrow
editor, never the input graph or execution ports as mutable shared state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

    from computer_use.interaction.read.presentation.ir import (
        ProjectionEditor,
        ProjectionResult,
        ProjectionView,
        ViewNode,
    )
    from computer_use.interaction.read.presentation.records import SemanticNode
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record
from dataclasses import dataclass

from computer_use.interaction.read.presentation.compression.model import (
    CompressionRun,
    RuleInvocation,
    Scope,
)
from computer_use.interaction.read.presentation.compression.passes.associate_labels import (
    associate_labels,
)
from computer_use.interaction.read.presentation.compression.passes.build_presentation_ir import (
    stage as build_presentation_ir,
)
from computer_use.interaction.read.presentation.compression.passes.collapse_transparent_groups import (
    collapse_transparent_groups,
)
from computer_use.interaction.read.presentation.compression.passes.compose_passive_text_lines import (
    stage as compose_passive_text_lines,
)
from computer_use.interaction.read.presentation.compression.passes.deduplicate_derived_container_captions import (
    deduplicate_derived_container_captions,
)
from computer_use.interaction.read.presentation.compression.passes.deduplicate_row_captions import (
    deduplicate_row_captions,
)
from computer_use.interaction.read.presentation.compression.passes.finalize_sources import (
    finalize_sources,
)
from computer_use.interaction.read.presentation.compression.passes.flatten_redundant_hierarchy import (
    flatten_redundant_hierarchy,
)
from computer_use.interaction.read.presentation.compression.passes.fold_native_caption_components import (
    fold_native_caption_components,
)
from computer_use.interaction.read.presentation.compression.passes.merge_control_labels import (
    merge_control_labels,
)
from computer_use.interaction.read.presentation.compression.passes.merge_editors import (
    merge_editors,
)
from computer_use.interaction.read.presentation.compression.passes.merge_owned_passive_content import (
    merge_owned_passive_content,
)
from computer_use.interaction.read.presentation.compression.passes.merge_region_labels import (
    merge_region_labels,
)
from computer_use.interaction.read.presentation.compression.passes.merge_repeated_text import (
    merge_repeated_text,
)
from computer_use.interaction.read.presentation.compression.passes.promote_description import (
    promote_description,
)
from computer_use.interaction.read.presentation.compression.passes.prune_empty_rows import (
    prune_empty_rows,
)
from computer_use.interaction.read.presentation.compression.passes.prune_layout import prune_layout
from computer_use.interaction.read.presentation.compression.passes.reconstruct_outlines import (
    reconstruct_outlines,
)
from computer_use.interaction.read.presentation.compression.passes.share_empty_descriptions import (
    share_empty_descriptions,
)
from computer_use.interaction.read.presentation.compression.passes.validate_projection import (
    stage as validate_projection,
)
from computer_use.interaction.read.presentation.ir import ProjectionInput
from computer_use.interaction.read.presentation.spatial.passes.protect_regions import stage as protect_spatial_regions
from computer_use.interaction.read.presentation.spatial.pipeline import SpatialOrganizationPipeline
from computer_use.interaction.read.presentation.tree import is_window
from computer_use.pipeline import Contract, Effect, Pass, Pipeline, Sequence
from computer_use.pipeline.core import Node

BEFORE_CHILDREN = (promote_description, merge_editors, merge_control_labels)
AFTER_CHILDREN = (
    merge_repeated_text,
    merge_region_labels,
    flatten_redundant_hierarchy,
    prune_layout,
    prune_empty_rows,
    collapse_transparent_groups,
)


@dataclass(frozen=True)
class Rule:
    """Compression handler and the presentation facts it reads and edits.

    Attributes:
        function: Compression handler invoked through the projection editor.
        reads: Presentation fact categories required by the handler.
        writes: Local presentation fields or relationships the handler may change.
    """

    function: Callable[..., Any]
    reads: tuple[str, ...]
    writes: tuple[str, ...]

    def pass_node(self) -> Pass[RuleInvocation, Any]:
        return Pass(
            self.function.__name__,
            lambda value, control: value.edit.run(self.function, value.view, *value.arguments),
            Contract(
                RuleInvocation,
                object,
                {Effect.PURE},
                requires=self.reads,
                writes=self.writes,
                failures=("presentation_contract",),
            ),
        )


RULES = {
    step: Rule(step, reads, writes)
    for step, reads, writes in (
        (share_empty_descriptions, ("complete empty descriptions", "source scopes"), ("shared field destinations",)),
        (
            associate_labels,
            ("label relations", "source scopes"),
            ("caption fields", "label elisions", "ordered children"),
        ),
        (promote_description, ("description", "control semantics"), ("caption fields",)),
        (
            merge_editors,
            ("control members", "values", "selection"),
            ("selection fields", "editor elisions", "ordered children"),
        ),
        (
            merge_control_labels,
            ("control members", "label facts"),
            ("caption fields", "label elisions", "ordered children"),
        ),
        (
            collapse_transparent_groups,
            ("local child coverage", "display boundary evidence"),
            ("transparent group elisions", "ordered children"),
        ),
        (merge_repeated_text, ("whole caption fields",), ("caption sources", "label elisions", "ordered children")),
        (merge_region_labels, ("heading children",), ("caption fields", "label elisions", "ordered children")),
        (flatten_redundant_hierarchy, ("layout boundaries",), ("splice elisions", "ordered children")),
        (prune_layout, ("layout boundaries",), ("splice elisions",)),
        (
            prune_empty_rows,
            (
                "row state",
                "focus",
            ),
            ("empty elisions",),
        ),
        (reconstruct_outlines, ("disclosure relations",), ("ordered children", "outline depth")),
        (
            deduplicate_row_captions,
            ("caption sources", "source scopes", "original containment"),
            ("caption fields", "caption destinations"),
        ),
        (
            deduplicate_derived_container_captions,
            ("complete retained caption fields", "independent primary targets", "source scopes"),
            ("derived container names", "caption destinations"),
        ),
        (
            fold_native_caption_components,
            ("complete native caption", "ordered passive text and input evidence"),
            ("caption component elisions", "native caption equivalence evidence"),
        ),
        (
            merge_owned_passive_content,
            ("complete local ownership", "exact caption sources", "display boundary evidence"),
            ("owned content elisions", "derived caption fields", "ordered children"),
        ),
        (
            finalize_sources,
            ("source routes", "elisions"),
            ("final source destinations", "folded source role declarations"),
        ),
    )
}


def apply_rule(step: Callable[..., Any], view: ProjectionView, edit: ProjectionEditor, *arguments: object) -> Any:
    return RULES[step].pass_node().run(RuleInvocation(view, edit, arguments))


def whole_rule(function: Callable[..., Any]) -> Pass[CompressionRun, CompressionRun]:
    """Wrap a whole-projection compression rule with its declared read/write contract.

    Args:
        function: Bound local handler; selecting it does not execute an unchosen branch.
    """
    rule = RULES[function]

    def invoke(run: CompressionRun, control: Control) -> CompressionRun:
        run.edit.run(function, run.view)
        return run

    return Pass(
        function.__name__,
        invoke,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=rule.reads,
            writes=rule.writes,
            failures=("presentation_contract",),
        ),
    )


class CompressionTraversal(Node["CompressionRun", "CompressionRun"]):
    """Apply node-local compression rules in one tree traversal.

    Run the pre-child rules, replace each child list with recursively compressed children,
    then apply post-child rules that may retain or splice the current node. Update the local
    projection roots and order application windows before other roots.
    """

    def __init__(self) -> None:
        super().__init__("CompressionTraversal", Contract(CompressionRun, CompressionRun, {Effect.PURE}))

    def _run(self, run: CompressionRun, control: Control) -> CompressionRun:
        view, edit = run.view, run.edit

        def visit(
            node: ViewNode, parent: ViewNode | None = None, in_table: bool = False
        ) -> list[ViewNode] | tuple[ViewNode, ...]:
            """Apply pre-order rules, rewrite children, then apply post-order replacement rules.

            Args:
                node: Source/projected node in this exact view; its native identity is
                    unchanged.
                in_table: Whether the current source path is inside a table.
            """
            scope = Scope(
                parent,
                node["role"] == "table"
                or (node["role"] == "row" and sum(child["role"] == "cell" for child in node["children"]) > 1)
                or (in_table and node["role"] != "outline"),
            )
            for step in BEFORE_CHILDREN:
                RULES[step].pass_node().run(RuleInvocation(view, edit, (node, scope)), control)
            edit.children(node, [item for child in node["children"] for item in visit(child, node, scope.in_table)])
            for step in AFTER_CHILDREN:
                replacement = RULES[step].pass_node().run(RuleInvocation(view, edit, (node, scope)), control)
                if len(replacement) != 1 or replacement[0] is not node:
                    return replacement
            return [node]

        if "app" in view.context and view.view["role"] == "application" and not view.context.get("subtreeRef"):
            edit.roots([item for child in view.view["children"] for item in visit(child)])
        else:
            edit.roots(visit(view.view))
        if "app" in view.context:
            edit.roots(
                [node for node in view.roots if is_window(node)]
                + [node for node in view.roots if not is_window(node) and node["role"] != "menuBar"]
                + [node for node in view.roots if node["role"] == "menuBar"]
            )
        return run

    async def _arun(self, run: CompressionRun, control: Control) -> CompressionRun:
        return self._run(run, control)


class CompressionPipeline(Pipeline["ProjectionInput", "ProjectionResult"]):
    """Compress one source-indexed display projection.

    Build the editable presentation IR, associate labels, and apply local and whole-
    projection rules. Finalize provenance destinations and export the validated projection
    for rendering.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[ProjectionInput, ProjectionResult]:
        return Sequence(
            name="CompressionPipeline",
            steps=[
                build_presentation_ir(),
                whole_rule(share_empty_descriptions),
                whole_rule(associate_labels),
                protect_spatial_regions(),
                compose_passive_text_lines(),
                CompressionTraversal(),
                whole_rule(reconstruct_outlines),
                SpatialOrganizationPipeline(),
                whole_rule(deduplicate_row_captions),
                whole_rule(deduplicate_derived_container_captions),
                whole_rule(fold_native_caption_components),
                whole_rule(merge_owned_passive_content),
                whole_rule(finalize_sources),
                validate_projection(),
            ],
        )


def project(root: SemanticNode, context: Record | None = None) -> ProjectionResult:
    return CompressionPipeline().run(ProjectionInput.build(root, context))
