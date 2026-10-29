# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.semantics.passes.build_projection_input import (
    stage as build_projection_input,
)
from computer_use.interaction.read.presentation.semantics.passes.compose_display_semantics import (
    stage as compose_display_semantics,
)
from computer_use.pipeline import Pipeline, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionInput
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import SemanticNode
    from computer_use.pipeline.core import Step


class SemanticPipeline(Pipeline["SemanticNode", "ProjectionInput"]):
    """Build the immutable source input for one root's display projection.

    Derive control and label composition from the semantic tree. Freeze the derived display
    fields, native sources, parent relationships, and branch context for compression.

    Attributes:
        branch: Root-specific native facts and presentation context used by semantic
            composition.
    """

    def __init__(self, branch: BranchFacts) -> None:
        self.branch = branch
        super().__init__(root=self.build())

    def build(self) -> Step[SemanticNode, ProjectionInput]:
        branch = self.branch
        return Sequence(
            name="SemanticPipeline",
            steps=[compose_display_semantics(), build_projection_input(branch=branch)],
        )
