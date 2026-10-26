"""Build projection input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.ir import ProjectionInput
from computer_use.interaction.read.presentation.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import SemanticNode
    from computer_use.pipeline.core import Control


def create(*, branch: BranchFacts) -> Callable[[SemanticNode, Control], ProjectionInput]:
    def execute(root: SemanticNode, control: Control) -> ProjectionInput:
        """Freeze the semantic root and branch context into source-indexed compression input."""
        return ProjectionInput.build(root, branch.context)

    return execute


stage = Pass.factory(
    name="build_projection_input",
    factory=create,
    contract=Contract(dict, ProjectionInput, PURE, writes=("read-only ProjectionInput",)),
)
