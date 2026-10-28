# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build projection input."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import SemanticNode
    from computer_use.pipeline.core import Control
from computer_use.interaction.read.presentation.compression.evidence import capture_evidence
from computer_use.interaction.read.presentation.ir import ProjectionInput
from computer_use.interaction.read.presentation.model import PURE
from computer_use.pipeline import Contract, Pass


def create(*, branch: BranchFacts) -> Callable[[SemanticNode, Control], ProjectionInput]:
    def execute(root: SemanticNode, control: Control) -> ProjectionInput:
        """Freeze the semantic root and branch context into source-indexed compression input."""
        context = branch.context
        context["deferVisualComposition"] = (
            branch.request.compact and branch.request.public_api and branch.request.share_descriptions
        )
        context["compressionEvidence"] = capture_evidence(branch.request.snapshot, branch.native_nodes)
        return ProjectionInput.build(root, context)

    return execute


stage = Pass.factory(
    name="build_projection_input",
    factory=create,
    contract=Contract(dict, ProjectionInput, PURE, writes=("read-only ProjectionInput",)),
)
