"""Validate directory scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.directory.model import DirectoryPlan
from computer_use.native.contracts import Binding
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate_directory_scope(plan: DirectoryPlan, control: Control) -> DirectoryPlan:
    """Require a session-level binding for the native directory query."""
    target = plan.read.target
    if target != Binding(target.session, None, None, None):
        raise ValueError("Directory collection requires a session read operation")
    return plan


stage = Pass.define(
    name="validate_directory_scope",
    function=validate_directory_scope,
    contract=Contract(DirectoryPlan, DirectoryPlan, {Effect.PURE}),
)
