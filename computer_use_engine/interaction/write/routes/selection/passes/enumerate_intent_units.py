"""Enumerate intent units."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.candidates import enumerate_intent_units
from computer_use.interaction.write.routes.selection.model import CandidateSet, SelectionRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: SelectionRequest, control: Control) -> CandidateSet:
    """Collect the native units whose declared operation set matches the requested intent."""
    return enumerate_intent_units(value)


stage = Pass.define(
    name="enumerate_intent_units",
    function=execute,
    contract=Contract(SelectionRequest, CandidateSet),
)
