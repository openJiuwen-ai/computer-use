"""Keep components."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import PURE, ClassifiedAttempt
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ClassifiedAttempt, control: Control) -> ClassifiedAttempt:
    """Carry the completed collection decision to the acquisition gate."""
    return value


stage = Pass.define(
    name="keep_components",
    function=execute,
    contract=Contract(ClassifiedAttempt, ClassifiedAttempt, PURE),
)
