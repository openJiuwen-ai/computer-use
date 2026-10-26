"""Validate fixed session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.scope.model import _ScopeLease
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate(value: _ScopeLease, control: Control) -> _ScopeLease:
    """Require the fixed working-window binding and open lease to share a driver session."""
    if value.read.target.session != value.request.selection.root.session:
        from computer_use.interaction.read.app import failure

        raise failure("stale_target", "The selected application belongs to a previous driver session.")
    return value


stage = Pass.define(
    name="validate_fixed_session",
    function=validate,
    contract=Contract(_ScopeLease, _ScopeLease, {Effect.PURE}),
)
