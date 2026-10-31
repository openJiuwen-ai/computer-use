# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Determine App-instance membership from confirmed window-parent paths."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

from computer_use.native.contracts import Knowledge
from computer_use.session.interfaces import DirectoryMembership, WindowIdentity, WindowMembership

if TYPE_CHECKING:
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.protocol.records import SurfaceInfo


def ancestry(directory: DirectoryFacts, window: str, root: str) -> Knowledge:
    """Prove containment or exclusion; unread/malformed paths remain unknown."""
    items = {item["window"]: item for item in directory["surfaces"]}
    seen = set()
    while window is not None:
        if window == root:
            return Knowledge.YES
        if window in seen:
            return Knowledge.UNKNOWN
        seen.add(window)
        item = items.get(window)
        if item is None or item["parentState"] == "unknown":
            return Knowledge.UNKNOWN
        if item["parentState"] == "none":
            return Knowledge.NO
        window = item["parent"]
    return Knowledge.UNKNOWN


def membership_evidence(directory: DirectoryFacts, root: str) -> DirectoryMembership:
    """Separate catalogue completeness from each window's relationship evidence."""
    return DirectoryMembership(
        not directory["stopReasons"],
        tuple(
            WindowMembership(WindowIdentity.of(item), ancestry(directory, item["window"], root))
            for item in directory["surfaces"]
        ),
    )


def ancestor(directory: DirectoryFacts, window: str, root: str) -> bool:
    """Return whether a window is the root or a confirmed descendant of it.

    Args:
        directory: Window catalogue containing parent relationships.
        window: Window key whose ancestry is being checked.
        root: Instance/window key that bounds the requested scope.
    """
    return ancestry(directory, window, root) is Knowledge.YES


def roots(directory: DirectoryFacts) -> list[SurfaceInfo]:
    """List non-menu windows whose parent is outside the confirmed catalogue."""

    def _iter_item(_source_items: Iterator[Any]) -> Iterator[Any]:
        for item in _source_items:
            if item["kind"] != "menu" and (
                item["parentState"] != "known"
                or not any(other["window"] == item["parent"] for other in directory["surfaces"])
            ):
                yield item

    return list(_iter_item(iter(directory["surfaces"])))


def automatic_root(directory: DirectoryFacts) -> SurfaceInfo | None:
    """Select one proved root without claiming its descendant discovery is complete.

    macOS discovers attached surfaces only by following a candidate window's
    child edges. Failure in that traversal can omit descendants, but cannot
    omit another independent root. Keep those gaps in the original directory;
    every other discovery gap and every unknown parent path still needs an
    explicit choice. The native protocol currently publishes these scoped
    failures as strings, so accept only its exact attached-traversal categories.
    """
    candidates = roots(directory)
    if len(candidates) != 1 or candidates[0]["parentState"] != "none":
        return None
    root = candidates[0]
    if any(ancestry(directory, item["window"], root["window"]) is Knowledge.UNKNOWN for item in directory["surfaces"]):
        return None
    attached_limits = {
        "attached surface discovery incomplete: child/node budget or changing relation",
        "attached surface discovery incomplete: time budget",
        "attached surface discovery incomplete: time/node budget",
    }
    for reason in directory["stopReasons"]:
        if directory.get("platform") != "macos" or not (
            reason.startswith("attached surface children unavailable: ") or reason in attached_limits
        ):
            return None
    return root


def in_scope(ui: UIHandle, surface: str | None) -> bool:
    """Check whether a native surface belongs to the App/Window view."""
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    instance = ui._instance
    item = next((item for item in instance.members.values() if item["surface"] == surface), None)
    return item is not None and (
        # pylint: disable-next=protected-access
        ui._anchor is None or ancestor(cast("DirectoryFacts", instance.directory), item["window"], ui._anchor)
    )
