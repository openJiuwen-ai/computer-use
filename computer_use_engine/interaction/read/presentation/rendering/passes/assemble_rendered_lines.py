"""Assemble rendered lines."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree


def execute(frame: LayoutFrame, _: object) -> RenderedTree:
    """Finalize the laid-out semantic lines and accumulated layout records into a rendered
    tree.
    """
    return frame.finalize(frame.lines, list(frame.records))


stage = Pass.define(
    name="assemble_rendered_lines",
    function=execute,
    contract=Contract(LayoutFrame, dict, PURE, requires=("packed semantic lines", "actual field records")),
)
