"""Startupdirectorygate."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.app_read.startup.model import PURE, StartupAttempt
from computer_use.pipeline import Continue, Contract, Done, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.app_read.startup.model import StartupDirectoryState
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import ApplicationInfo, NativeDirectory as DirectoryFacts


def startup_directory_gate(
    value: StartupAttempt, control: Control
) -> Continue[StartupDirectoryState] | Done[tuple[ApplicationInfo, DirectoryFacts]]:
    """Finish startup discovery or advance its bounded catalogue-read attempt.

    A running surface, partial catalogue, non-launched scope, or final attempt completes the
    read. Retry an eligible empty or stale startup result until the gate's attempt limit.
    """
    state, directory = value.state, cast("DirectoryFacts", value.directory)
    if value.stale is not None:
        if state.attempt == 4:
            raise value.stale
    elif not state.launched or directory["surfaces"] or directory["stopReasons"] or state.attempt == 4:
        return Done((state.application, directory))
    return Continue(replace(state, attempt=state.attempt + 1))


stage = Pass.define(
    name="StartupDirectoryGate",
    function=startup_directory_gate,
    contract=Contract(StartupAttempt, object, PURE),
)
