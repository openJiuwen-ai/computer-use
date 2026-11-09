# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit window pixels, independent of accessibility nodes and tree rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from computer_use.native.contracts import Binding
from computer_use.protocol.captures import capture_metadata
from computer_use.protocol.png import PNG
from computer_use.protocol.primitives import frozen
from computer_use.session.interfaces import WindowIdentity

if TYPE_CHECKING:
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import NativeDirectory


ImageMemberScope = tuple[str, tuple[WindowIdentity, ...]]


@dataclass(frozen=True)
class WindowImage:
    """Captured window pixels with native identity, capture time, and coordinate metadata.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        observed: Capture provenance or original observation used by the current stage.
        coordinates: Original native-to-image coordinate map with its declared coordinate space.
        scope_changed: An armed native window or menu change notification arrived during capture.
        rechecked_scope: Exact member path confirmed after capture in an incomplete directory.
    """

    target: Binding
    image: PNG
    observed: Mapping[str, Any]
    coordinates: Mapping[str, Any]
    scope_changed: bool = False
    rechecked_scope: ImageMemberScope | None = None

    def member_scope(self, directory: NativeDirectory) -> ImageMemberScope | None:
        """Identify this native member and its known parent chain independently of peers."""
        return self._member_scope(directory, require_native=True)

    def _member_scope(self, directory: NativeDirectory, *, require_native: bool) -> ImageMemberScope | None:
        target = self.target
        if directory.get("platform") != "macos" or directory["session"] != target.session:
            return None
        members = {item["window"]: item for item in directory["surfaces"]}
        window = target.root_window
        member = members.get(window)
        if member is None or require_native and not member["nativeWindow"]:
            return None
        if (member["instance"], member["surface"]) != (target.instance, target.surface):
            return None
        ended = {WindowIdentity.of(item) for item in directory.get("endedWindows", ())}
        path, seen = [], set()
        while window in members and window not in seen:
            member = members[window]
            identity = WindowIdentity.of(member)
            if identity in ended:
                break
            path.append(identity)
            seen.add(window)
            parent, state = member["parent"], member["parentState"]
            if state == "none" and parent is None:
                return directory["application"], tuple(path)
            if state != "known":
                break
            window = parent
        return None

    def native_upgrade_scope(
        self, source: NativeDirectory, current: NativeDirectory,
    ) -> ImageMemberScope | None:
        """Match a newly native member to its exact earlier AX-only lifetime."""
        earlier = next((item for item in source["surfaces"] if item["window"] == self.target.root_window), None)
        if earlier is None or earlier["nativeWindow"] is not False or not earlier["accessibility"]:
            return None
        confirmed = self.member_scope(current)
        if confirmed is not None and confirmed == self._member_scope(source, require_native=False):
            return confirmed
        return None


class NativeImages:
    """Capture native window pixels and retain their geometry and provenance."""

    def __init__(self, port: NativePort) -> None:
        self.port = port

    async def window(self, target: Binding, *, max_dimension: int = 2048, timeout_ms: int = 30000) -> WindowImage:
        """Capture the selected window and retain its native geometry and capture metadata.

        Args:
            target: Native identity within the current application and window scope.
            max_dimension: Maximum image edge length in pixels, preserving aspect ratio.
            timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
        """
        if target.instance is None or target.surface is None:
            raise ValueError("Window capture requires an observed working-window binding")
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(max_dimension) is not int or not 1 <= max_dimension <= 4096:
            raise ValueError("Image dimension budget must be an integer in 1..4096")
        # A new observation creates its own image binding. It never updates an
        # existing Element, capture, or operation's coordinate target in place.
        target = replace(target, node=None, capture=None)
        async with self.port.calls.open(target, timeout_ms) as read:
            return await self.collect(read, max_dimension=max_dimension)

    async def collect(self, read: OperationLease[Binding], *, max_dimension: int = 2048) -> WindowImage:
        """Capture the exact window of an existing window read operation.

        Args:
            read: Open native operation lease; owns the token and original millisecond deadline.
            max_dimension: Maximum image edge length in pixels, preserving aspect ratio.
        """
        target = read.target
        invalid_target = (
            True
            if target.instance is None
            or target.surface is None
            or target.node is not None
            or target.capture is not None
            else False
        )
        if invalid_target:
            raise ValueError("Image collection requires an un-narrowed window read operation")
        # pylint: disable-next=unidiomatic-typecheck
        if type(max_dimension) is not int or not 1 <= max_dimension <= 4096:
            raise ValueError("Image dimension budget must be an integer in 1..4096")
        result = await self.port.capture(read.token, target, max_dimension, read.remaining())
        image = result["image"]
        value = WindowImage(
            replace(target, capture=capture_metadata(image)["id"]),
            image,
            result["observed"],
            frozen(result["coordinates"]),
            scope_changed=result.get("windowScopeChanged", False),
        )
        return value
