# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure selection evidence for one bounded observation after an action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from weakref import ReferenceType

from computer_use.interaction.query.instances.membership import ancestor
from computer_use.native.contracts import Binding
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.surface_content import content_kind

if TYPE_CHECKING:
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.records import NativeDirectory, SurfaceInfo

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
    window_family_application: str | None = None
    selection_target: Binding | None = None
    seek_editor: bool = False

    @property
    def window_family_only(self) -> bool:
        return self.window_family_application is not None

    @property
    def original_window(self) -> WindowIdentity | None:
        if self.window_family_only:
            return WindowIdentity(self.original.root_window, self.original.surface, self.original.instance)
        return next((item for item in self.known_windows if item.window == self.original.root_window), None)

    def content_members(
        self, directory: NativeDirectory, members: dict[str, SurfaceInfo] | None = None,
    ) -> dict[str, SurfaceInfo]:
        """Separate eligible automatic content from the full discovery catalogue."""
        if members is None:
            members = {item["window"]: item for item in directory["surfaces"]}
        if not self.window_family_only:
            return members
        if (
            directory["session"] != self.original.session
            or directory["application"] != self.window_family_application
        ):
            return {}
        roots = set()
        for identity in (self.original_window, self.context_root):
            if identity is None or identity.window not in members:
                continue
            if WindowIdentity.of(members[identity.window]) == identity:
                roots.add(identity.window)
        return {
            key: item for key, item in members.items()
            if any(ancestor(directory, key, root) for root in roots)
        }

    def recovery_window(self, directory, unavailable: frozenset[str] = frozenset()) -> str | None:
        """Find a surviving original scope after collection updates its directory.

        A closing modal can be present during initial selection and absent by
        the component recheck. A fixed close stays within its window family.
        Other related reads can fall back to the App's current window.
        """
        current = {WindowIdentity.of(item) for item in directory["surfaces"]}
        for identity in (self.original_window, self.context_root):
            if identity is not None and identity in current and identity.window not in unavailable:
                return identity.window
        if self.window_family_only:
            return None
        windows = [
            item["window"] for item in directory["surfaces"]
            if content_kind(item) == "window" and item["window"] not in unavailable
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
            for item in self.content_members(directory).values()
            if content_kind(item) == "window" and WindowIdentity.of(item) not in self.known_windows
        )

    def new_surfaces(self, directory) -> tuple[str, ...]:
        """New content includes menus without promoting them to working windows."""
        return tuple(
            item["window"] for item in self.content_members(directory).values()
            if WindowIdentity.of(item) not in self.known_windows
        )

    def preferred(self, directory, fallback: str) -> str:
        if self.window_family_only:
            current = {WindowIdentity.of(item) for item in directory["surfaces"]}
            if self.original_window not in current and self.context_root in current:
                return self.context_root.window
            return self.original.root_window
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
        if not any(item["window"] == fallback for item in directory["surfaces"]):
            return self.recovery_window(directory) or fallback
        return fallback


def window_lifecycle(identity: WindowIdentity, directory) -> str:
    """Describe the original lifetime from platform directory evidence."""
    if any(WindowIdentity.of(item) == identity for item in directory.get("endedWindows", ())):
        return "closed"
    item = next((item for item in directory["surfaces"] if item["window"] == identity.window), None)
    if item is not None:
        return "observed" if WindowIdentity.of(item) == identity else "closed"
    return (
        "closed"
        if not directory["stopReasons"] and directory.get("platform") == "linux"
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
