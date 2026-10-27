"""Explicit window pixels, independent of accessibility nodes and tree rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from computer_use.native.contracts import Binding
from computer_use.protocol.captures import capture_metadata
from computer_use.protocol.png import PNG
from computer_use.protocol.primitives import frozen

if TYPE_CHECKING:
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort


@dataclass(frozen=True)
class WindowImage:
    """Captured window pixels with native identity, capture time, and coordinate metadata.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        observed: Capture provenance or original observation used by the current stage.
        coordinates: Original native-to-image coordinate map with its declared coordinate space.
    """

    target: Binding
    image: PNG
    observed: Mapping[str, Any]
    coordinates: Mapping[str, Any]


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
        )
        read.remaining()
        return value
