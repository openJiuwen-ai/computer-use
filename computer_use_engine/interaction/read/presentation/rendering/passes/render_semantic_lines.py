"""Render semantic lines."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.rendering.formatting import renderProjectionLines
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.pipeline.core import Control


def create(*, branch: BranchFacts) -> Callable[[ProjectionResult, Control], LayoutFrame]:
    def render(projection: ProjectionResult, control: Control) -> LayoutFrame:
        """Render the branch's compressed source projection into semantic lines and field-emission
        records.
        """
        return renderProjectionLines(projection["nativeNodes"][branch.root["ref"]], branch.context, projection)

    return render


stage = Pass.factory(
    name="render_semantic_lines",
    factory=create,
    contract=Contract(
        dict,
        LayoutFrame,
        PURE,
        requires=("final forest", "source facts"),
        writes=("text", "actual field records"),
    ),
)
