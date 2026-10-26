"""Resolve declared startup association."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.directory.service import select_application
from computer_use.interaction.read.app_read.startup.model import PURE, StartupLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def association(value: StartupLease, control: Control) -> StartupLease:
    """Resolve the installation's running catalogue entry and update its startup identity when
    found.
    """
    if value.applications["stopReasons"]:
        from computer_use.interaction.read.app import failure

        raise failure(
            "observation_incomplete",
            "Startup application discovery is partial; inspect the application directory before choosing an instance.",
        )
    selected = select_application(value.applications, value.state.installation)
    if not selected["isRunning"]:
        return value
    return replace(value, state=replace(value.state, application=selected, installation=None), associated=True)


stage = Pass.define(
    name="resolve_declared_startup_association",
    function=association,
    contract=Contract(StartupLease, StartupLease, PURE),
)
