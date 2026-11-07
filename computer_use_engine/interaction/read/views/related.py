# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure selection evidence for one bounded observation after an action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from weakref import ReferenceType

from computer_use.native.contracts import Binding
from computer_use.session.interfaces import WindowIdentity

if TYPE_CHECKING:
    from computer_use.interaction.records import UIHandle

    from .changes import PostwriteChanges


@dataclass(frozen=True)
class RelatedRead:
    """Original action target and the catalogue known before dispatch.

    Newly discovered windows are observation candidates, not evidence that the
    action created them. The original target remains independent of selection.
    menu_origin reserves exact tree work after an uncertain native menu invocation.
    """

    original: Binding
    known_windows: tuple[WindowIdentity, ...]
    context_owner: ReferenceType[UIHandle] | None = None
    context_root: WindowIdentity | None = None
    changes: PostwriteChanges | None = None
    menu_origin: WindowIdentity | None = None

    @property
    def original_window(self) -> WindowIdentity | None:
        return next((item for item in self.known_windows if item.window == self.original.root_window), None)

    def recovery_window(self, directory, unavailable: frozenset[str] = frozenset()) -> str | None:
        """Find a surviving original scope after collection updates its directory.

        A closing modal can be present during initial selection and absent by
        the component recheck. Prefer its surviving fixed parent, then the
        App's current window. This widens the follow-up observation only;
        publication separately prevents rebinding the original fixed handle.
        """
        current = {WindowIdentity.of(item) for item in directory["surfaces"]}
        for identity in (self.original_window, self.context_root):
            if identity is not None and identity in current and identity.window not in unavailable:
                return identity.window
        windows = [
            item["window"] for item in directory["surfaces"]
            if item["kind"] != "menu" and item["window"] not in unavailable
        ]
        focused = directory.get("focusedWindow")
        if focused in windows:
            return focused
        if len(windows) == 1:
            return windows[0]
        return None

    def new_windows(self, directory) -> tuple[str, ...]:
        return tuple(
            item["window"]
            for item in directory["surfaces"]
            if item["kind"] != "menu" and WindowIdentity.of(item) not in self.known_windows
        )

    def preferred(self, directory, fallback: str) -> str:
        newly_seen = self.new_windows(directory)
        focused = directory.get("focusedWindow")
        if focused in newly_seen:
            return focused
        independent = [
            item["window"] for item in directory["surfaces"]
            if item["window"] in newly_seen and (item["modal"] is True or item["parentState"] != "known")
        ]
        if len(independent) == 1:
            return independent[0]
        if any(item["window"] == self.original.root_window for item in directory["surfaces"]):
            return self.original.root_window
        if self.context_root is not None and any(
            WindowIdentity.of(item) == self.context_root for item in directory["surfaces"]
        ):
            return self.context_root.window
        return fallback


def window_lifecycle(identity: WindowIdentity, directory) -> str:
    """Describe native lifetime facts without treating hidden macOS windows as ended."""
    if any(WindowIdentity.of(item) == identity for item in directory.get("endedWindows", ())):
        return "closed"
    item = next((item for item in directory["surfaces"] if item["window"] == identity.window), None)
    if item is not None:
        return "observed" if WindowIdentity.of(item) == identity else "closed"
    return (
        "closed"
        if not directory["stopReasons"] and directory.get("platform") in ("windows", "linux")
        else "unknown"
    )


def original_lifecycle(target, directory, previous: WindowIdentity | None = None) -> str:
    """Compare the original window lifetime with one exact captured directory."""
    if target.root_window is None:
        return "not_applicable"
    if directory is None or directory["session"] != target.session:
        return "unknown"
    identity = previous or WindowIdentity(target.root_window, target.surface, target.instance)
    return window_lifecycle(identity, directory)
