"""Select collection kind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import PURE, ReadRunState
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ReadRunState, control: Control) -> bool:
    """Choose tree-only refresh when requested by the current acquisition attempt."""
    return value.refresh_tree


stage = Pass.define(
    name="select_collection_kind",
    function=execute,
    contract=Contract(ReadRunState, bool, PURE),
)
