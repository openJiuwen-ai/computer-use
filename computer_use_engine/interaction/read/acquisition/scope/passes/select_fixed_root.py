"""Select fixed root."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.scope.model import _ObservedDirectory
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.native.scopes import WorkingWindow
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


def create(
    *, services: NativeAppReader
) -> Callable[[_ObservedDirectory, Control], tuple[WorkingWindow, DirectoryFacts]]:
    def choose(value: _ObservedDirectory, control: Control) -> tuple[WorkingWindow, DirectoryFacts]:
        """Resolve the fixed working root against the collected directory and return both pieces of
        evidence.
        """
        selection, directory = value.request.selection, value.directory
        return services._selection(
            selection.application, selection.name, directory, selection.root.root_window, previous=selection
        ), directory

    return choose


stage = Pass.factory(
    name="select_fixed_root",
    factory=create,
    contract=Contract(_ObservedDirectory, tuple, {Effect.PURE}),
)
