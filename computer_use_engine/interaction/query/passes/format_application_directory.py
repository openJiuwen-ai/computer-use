"""Format application directory."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use._identity import FrozenRecord
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


def applications(directory: DirectoryFacts, control: Control) -> tuple[NativeApplicationSummary, ...]:
    """Project application catalogue rows into immutable summaries.

    Each summary carries the provider's native ID, display name, and running
    state for the internal application-list result.
    """
    return cast(
        "tuple[NativeApplicationSummary, ...]",
        tuple(
            FrozenRecord({key: item[key] for key in ("id", "displayName", "isRunning")})
            for item in directory["applications"]
        ),
    )


stage = Pass.define(
    name="format_application_directory",
    function=applications,
    contract=Contract(dict, tuple),
)
if TYPE_CHECKING:
    from computer_use.interaction.records import NativeApplicationSummary
