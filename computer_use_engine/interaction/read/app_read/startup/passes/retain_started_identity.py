"""Retain started identity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.startup.model import PURE, StartupLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: StartupLease, control: Control) -> StartupLease:
    """Carry the established running identity directly to surface collection."""
    return value


stage = Pass.define(
    name="retain_started_identity",
    function=execute,
    contract=Contract(StartupLease, StartupLease, PURE),
)
