"""Validate explicit startup."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.app import failure
from computer_use.interaction.read.app_read.model import PURE, ApplicationSelection
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate(value: ApplicationSelection, control: Control) -> ApplicationSelection:
    """Validate launch permission and the absent window constraint, then retain installation
    identity.
    """
    if value.request.window is not None:
        raise failure("stale_target", "An installed, nonrunning application cannot contain the requested window.")
    if not value.request.allow_launch:
        raise failure(
            "observation_incomplete", "The selected application is not running; launch is disabled for this read."
        )
    return replace(value, installation=value.application["id"] if value.application.get("matchKeys") else None)


stage = Pass.define(
    name="validate_explicit_startup",
    function=validate,
    contract=Contract(ApplicationSelection, ApplicationSelection, PURE),
)
