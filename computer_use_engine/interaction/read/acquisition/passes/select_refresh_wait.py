"""Select refresh wait."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import PURE, ClassifiedAttempt, Refresh
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ClassifiedAttempt, control: Control) -> Refresh:
    """Return the classified refresh kind used to select the corresponding wait branch."""
    return value.refresh


stage = Pass.define(
    name="select_refresh_wait",
    function=execute,
    contract=Contract(ClassifiedAttempt, Refresh, PURE),
)
