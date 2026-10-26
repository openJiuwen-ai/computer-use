"""Shared application identities and fixed working-window bindings."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from computer_use.errors import ComputerUseError

from .contracts import Binding


class FixedWindowInvalidated(ComputerUseError):
    """Exact old root disproved by a complete directory or replacement identity.

    Only the fixed-window selector emits this evidence. Generic stale/query
    errors carry no lifecycle authority and must not retire an App instance.
    """

    def __init__(self, binding: Binding, detail: str):
        self.binding = binding
        super().__init__(
            dict(
                code="stale_target",
                effect="none",
                target="working window",
                detail=detail,
                next="discover a new App; this fixed window will not follow another document",
            )
        )


class FixedWindowUnconfirmed(ComputerUseError):
    """A partial directory could not observe the fixed root; identity is retained."""

    def __init__(self, binding: Binding):
        self.binding = binding
        super().__init__(
            dict(
                code="observation_incomplete",
                effect="none",
                target="working window",
                detail="The fixed working window is missing from the partial current directory.",
                next="read this App/Window again to refresh its evidence",
            )
        )


def window_binding(binding):
    return replace(binding, node=None, capture=None)


@dataclass(frozen=True)
class RunningApplication:
    """An observed instance, before its working window has been selected."""

    application: str
    name: str
    session: str


@dataclass(frozen=True)
class SelectedApplicationWindow(RunningApplication):
    """An exact directory choice, retained even before its first read succeeds."""

    window: str


@dataclass(frozen=True)
class StartingApplication(RunningApplication):
    """A dispatched launch before a declared running application is selected.

    The direct process receipt is retained separately from the eventual window
    owner. Reading this scope can only observe; it never repeats the launch.
    """

    installation: str
    launch: Mapping


@dataclass(frozen=True)
class WorkingWindow:
    application: str
    name: str
    root: Binding
