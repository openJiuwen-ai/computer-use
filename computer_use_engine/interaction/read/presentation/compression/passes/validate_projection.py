"""Validate source-preserving projection before exporting it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.ir import export_projection
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.pipeline.core import Control


def validate_projection(run: CompressionRun, control: Control) -> ProjectionResult:
    """Validate and export the completed projection with its source and field-provenance
    ledger.
    """
    return export_projection(run.ir)


stage = Pass.define(
    name="validate_projection",
    function=validate_projection,
    contract=Contract(
        CompressionRun,
        dict,
        {Effect.PURE},
        requires=("source facts", "final forest"),
        failures=("presentation_contract",),
    ),
)
