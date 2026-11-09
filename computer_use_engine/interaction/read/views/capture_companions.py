# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local capture association, independent of App selection and input permission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.protocol.captures import capture_metadata
from computer_use.session.interfaces import WindowIdentity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from computer_use.protocol.records import NativeDirectory, SurfaceInfo

    from .pipeline import WindowPart


@dataclass(frozen=True)
class CompanionCapture:
    """One exact successful parent image between two actual directory samples."""

    parent: WindowIdentity
    capture: str
    before: str
    after: str


@dataclass(frozen=True)
class CapturedSharingControl:
    """Positive native control signature, associated with that parent capture."""

    context: CompanionCapture
    root: str
    button: str


def companion_capture(item: SurfaceInfo, directory: NativeDirectory, parts: Sequence[WindowPart]):
    """Select only a newly observed nonmodal child of a successfully captured parent.

    This is permission to inspect a small source tree, never classification by
    itself. Partial unrelated App catalogue facts do not invalidate this local
    association, and observing an entry later does not prove its creation time.
    """
    if directory.get("platform") != "macos" or item["kind"] != "dialog" or item["modal"] is not False:
        return None
    if item["parentState"] != "known" or item["parent"] is None or item.get("node") is None:
        return None
    if item.get("accessibility") is not True or directory.get("focusedWindow") == item["window"]:
        return None
    parent = next((row for row in directory["surfaces"] if row["window"] == item["parent"]), None)
    if parent is None:
        return None
    identity = WindowIdentity.of(parent)
    for part in parts:
        image = part.image
        if image is None or WindowIdentity(
            image.target.root_window, image.target.surface, image.target.instance
        ) != identity or image.target.session != directory["session"]:
            continue
        before = part.image_directory
        if before is None and part.source is not None:
            before = part.source.source_directory
        if before is None or before["session"] != directory["session"]:
            continue
        if before["application"] != directory["application"]:
            continue
        if any(WindowIdentity.of(row) == WindowIdentity.of(item) for row in before["surfaces"]):
            continue
        if not any(WindowIdentity.of(row) == identity for row in before["surfaces"]):
            continue
        first, last = before.get("observed"), directory.get("observed")
        capture = capture_metadata(image.image)
        if first is None or last is None or first["id"] == last["id"]:
            continue
        if capture.get("kind") != "window" or capture.get("id") != image.target.capture:
            continue
        if capture.get("scope") != image.target.surface:
            continue
        if not first["completedAt"] <= capture["startedAt"] <= capture["completedAt"] <= last["startedAt"]:
            continue
        return CompanionCapture(identity, capture["id"], first["id"], last["id"])
    return None
