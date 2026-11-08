# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared application identities and fixed working-window bindings."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from computer_use.errors import ComputerUseError

from .contracts import Binding


class FixedWindowInvalidated(ComputerUseError):
    """Exact old root disproved by native lifetime or replacement evidence.

    This evidence requires an exact window or application lifetime. Generic
    stale/query errors cannot retire an App instance.
    """

    def __init__(self, binding: Binding, detail: str, *, directory=None):
        self.binding = binding
        self.directory = directory
        super().__init__(
            dict(
                code="stale_target",
                effect="none",
                target="working window",
                detail=detail,
            )
        )


class FixedWindowUnconfirmed(ComputerUseError):
    """The current catalogue did not observe the fixed root; identity is retained."""

    def __init__(self, binding: Binding, *, directory=None):
        self.binding = binding
        self.directory = directory
        super().__init__(
            dict(
                code="observation_incomplete",
                effect="none",
                target="working window",
                detail=(
                    "The fixed working window is not present in the current observed catalogue; "
                    "its lifetime is unconfirmed."
                ),
            )
        )


class QueryRootUnavailable(ComputerUseError):
    """The exact AX query proxy failed; this has no Window lifetime authority."""

    def __init__(self, binding: Binding, *, native_domain: str, native_code: int):
        self.binding = binding
        self.native_domain, self.native_code = native_domain, native_code
        super().__init__({
            "code": "observation_incomplete", "effect": "none", "target": "element path",
            "detail": "The retained native query root is unavailable.",
        })


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
class PendingApplicationLaunch:
    """Original installation retained when launch may have reached the system."""

    session: str
    installation: str
    selectors: tuple[str, ...]


@dataclass(frozen=True)
class WorkingWindow:
    application: str
    name: str
    root: Binding
