# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Confirm explicit Window containment independently of application ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.native.contracts import Knowledge
from computer_use.session.surface_content import content_within

if TYPE_CHECKING:
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


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


def ancestor(directory: DirectoryFacts, window: str, root: str) -> bool:
    """Return whether a window is the root or a confirmed descendant of it.

    Args:
        directory: Window catalogue containing parent relationships.
        window: Window key whose ancestry is being checked.
        root: Instance/window key that bounds the requested scope.
    """
    return ancestry(directory, window, root) is Knowledge.YES


def in_scope(ui: UIHandle, surface: str | None) -> bool:
    """Check whether a native surface belongs to the App/window view."""
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    instance = ui._instance
    # pylint: disable-next=protected-access
    anchor = ui._anchor
    item = next((item for item in instance.members.values() if item["surface"] == surface), None)
    if getattr(ui, "_strict_window", False):
        return item is not None and item["window"] == anchor
    return item is not None and (
        anchor is None or interaction_contains(cast("DirectoryFacts", instance.directory), item["window"], anchor)
    )


def interaction_contains(directory: DirectoryFacts, window: str, root: str) -> bool:
    """Observation/name-query scope includes proved attached control content."""
    return content_within({item["window"]: item for item in directory["surfaces"]}, window, root)
