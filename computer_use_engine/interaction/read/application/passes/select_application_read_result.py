"""Select application read result."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.application.model import AppReadResult
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    pass


def execute(value: AppReadResult, _: object) -> bool:
    """Choose window-choice publication when application acquisition requires disambiguation."""
    return value.choices


stage = Pass.define(
    name="select_application_read_result",
    function=execute,
    contract=Contract(AppReadResult, bool),
)
