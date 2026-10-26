"""Select captured text source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import PURE, CapturedTextPlan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(plan: CapturedTextPlan, control: Control) -> bool:
    """Choose local text paging when the complete captured value is retained."""
    return plan.request.source.value is not None


stage = Pass.define(
    name="select_captured_text_source",
    function=execute,
    contract=Contract(CapturedTextPlan, bool, PURE),
)
