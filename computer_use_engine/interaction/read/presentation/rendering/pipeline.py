# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.pipeline.core import Step
from computer_use.interaction.read.presentation.interface.rendering.pipeline import InterfaceFormattingPipeline
from computer_use.interaction.read.presentation.rendering.layout.pipeline import LayoutPipeline
from computer_use.interaction.read.presentation.rendering.passes.assemble_rendered_lines import (
    stage as assemble_rendered_lines,
)
from computer_use.interaction.read.presentation.rendering.passes.render_semantic_lines import (
    stage as render_semantic_lines,
)
from computer_use.pipeline import Pipeline, Sequence


class RenderPipeline(Pipeline["ProjectionResult", "RenderedTree"]):
    """Render the compressed root and apply its text layout.

    Build semantic display lines and source-emission metadata, then select natural compact/
    subtree lines or full diagnostic layout. Finalize the result with layout records and display
    references.

    Attributes:
        branch: Original root and formatting context used while rendering the projection.
    """

    def __init__(
        self,
        branch: BranchFacts,
        *,
        interface: InterfaceDocument | None = None,
        disclosure: DisclosurePlan | None = None,
    ) -> None:
        self.branch = branch
        self.interface = interface
        self.disclosure = disclosure
        super().__init__(root=self.build())

    def build(self) -> Step[ProjectionResult, RenderedTree]:
        branch = self.branch

        return Sequence(
            name="RenderPipeline",
            steps=[
                render_semantic_lines(branch=branch, interface=self.interface, disclosure=self.disclosure),
                LayoutPipeline(),
                *([InterfaceFormattingPipeline()] if self.interface is not None and self.interface.decisions else []),
                assemble_rendered_lines(),
            ],
        )
