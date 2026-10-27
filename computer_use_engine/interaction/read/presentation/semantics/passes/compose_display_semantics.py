"""Compose display semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.semantics.controls import compose_controls
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import SemanticNode
    from computer_use.pipeline.core import Control


def compose_display_semantics(root: SemanticNode, control: Control) -> SemanticNode:
    """Derive source-backed control, label, and editor composition for the root."""
    return compose_controls(root, fact_backed=True)


stage = Pass.define(
    name="compose_display_semantics",
    function=compose_display_semantics,
    contract=Contract(
        dict,
        dict,
        PURE,
        requires=("normalized native facts",),
        writes=("derived control semantics",),
    ),
)
