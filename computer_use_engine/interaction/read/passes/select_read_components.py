"""Select read components."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.model import ApplicationSelection
from computer_use.interaction.read.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ApplicationSelection, control: Control) -> bool:
    """Choose menu observation when requested or when the application has no working window."""
    return value.request.menu_path is not None or value.selection is None


stage = Pass.define(
    name="select_read_components",
    function=execute,
    contract=Contract(ApplicationSelection, bool, PURE),
)
