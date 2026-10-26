"""Remember associated running scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.startup.model import RESOURCE, StartupLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def remember(value: StartupLease, control: Control) -> StartupLease:
    """Notify the scope owner when installation association confirms a running application."""
    if value.associated and value.state.on_scope is not None:
        from computer_use.interaction.read.app import RunningApplication

        application = value.state.application
        value.state.on_scope(RunningApplication(application["id"], application["displayName"], value.state.session))
    return value


stage = Pass.define(
    name="remember_associated_running_scope",
    function=remember,
    contract=Contract(StartupLease, StartupLease, RESOURCE),
)
