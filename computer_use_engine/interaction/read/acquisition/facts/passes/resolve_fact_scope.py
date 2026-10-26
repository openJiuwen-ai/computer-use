"""Resolve fact scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.helpers import resolve_fact_scope
from computer_use.interaction.read.acquisition.facts.model import PURE, FactReadRequest, FactScope
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.pipeline.core import Control


def create(*, reader: NativeReader) -> Callable[[FactReadRequest, Control], FactScope]:
    def execute(request: FactReadRequest, control: Control) -> FactScope:
        """Validate and resolve the fact request's exact native element or window scope."""
        return resolve_fact_scope(request, control, validate=reader.validate)

    return execute


stage = Pass.factory(
    name="resolve_fact_scope",
    factory=create,
    contract=Contract(FactReadRequest, FactScope, PURE),
)
