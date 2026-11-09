# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured window composition and coordinate mapping for each UI view."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import TYPE_CHECKING, Protocol, TypeAlias

from computer_use.diagnostics import local_failure
from computer_use.protocol.captures import capture_metadata
from computer_use.session.image_coordinates import ImageCoordinates
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from computer_use.native.contracts import Binding
    from computer_use.protocol.png import PNG
    from computer_use.session.interfaces import WindowIdentity
    from computer_use.types import Point

ImageBox: TypeAlias = tuple[float, float, float, float]
Membership: TypeAlias = tuple[tuple[str, str, str | None, str, bool | None], ...]


class CapturedWindow(Protocol):
    """Window identity, pixels, and transform used by image composition."""

    @property
    def target(self) -> Binding:
        pass

    @property
    def image(self) -> PNG:
        pass

    @property
    def coordinates(self) -> Mapping[str, Any]:
        pass


@dataclass(frozen=True)
class ImagePiece:
    """One original capture placed into a composed view.

    Attributes:
        window: Exact member-window identity.
        image: Original capture and its retained native coordinate map.
        box: Placement rectangle in composed-image pixels.
        scope_confirmed: Whether this capture can contribute current coordinate authority.
    """

    window: str
    image: CapturedWindow
    box: ImageBox
    scope_confirmed: bool = True


@dataclass
class InterfaceImage:
    """Per-view pixels and input authority retained by the image owner.

    Attributes:
        png: Composed or original PNG bytes.
        pieces: Original capture mappings, ordered for hit testing.
        membership: Member/modal layout observed with this canvas.
        origin: Native-space canvas origin used by composition.
        scale: Native-to-canvas scale.
        delivered: Epoch when these pixels were returned to the model, or None.
        digest: SHA-256 digest of the retained PNG.
        expired: Whether the existing mapping has been invalidated.
        unavailable_mapping: Whether partial pixels lack an overall coordinate map.
        delivered_coordinates: Actual model-image pixels mapped to this retained canvas.
        generation: Acquisition order within the owning application.
        source_canvas: Shared original canvas kept alive by its fixed-window views.
        identities: Captured generations for host/source aliases, never rebound at delivery.
    """

    png: PNG
    pieces: tuple[ImagePiece, ...]
    membership: Membership
    origin: Point = (0, 0)
    scale: float = 1.0
    delivered: int | None = None
    digest: bytes = b""
    expired: bool = False
    unavailable_mapping: bool = False
    delivered_coordinates: ImageCoordinates | None = None
    generation: int = 0
    source_canvas: InterfaceImage | None = field(default=None, repr=False)
    content_hosts: tuple[tuple[str, str], ...] = ()
    shared_pixels: tuple[tuple[str, str], ...] = ()
    identities: tuple[WindowIdentity, ...] = ()

    def for_window(self, window: str) -> InterfaceImage | None:
        """Keep this displayed canvas, restricted to one fixed window's scope.

        Pixel coordinates stay in the original displayed PNG, including any
        composition offsets and delivery scaling. Other members' pixels do not
        acquire input authority merely because they occur in that same PNG.
        """
        members = {item[0]: item for item in self.membership}
        hosts = dict(self.content_hosts)
        scope = dict(self.shared_pixels).get(window, window)

        def within(key: str) -> bool:
            pending, seen = [key], set()
            while pending:
                key = pending.pop()
                if key == scope:
                    return True
                if key in seen:
                    continue
                seen.add(key)
                item = members.get(key)
                if item is not None and item[3] == "known" and item[2] is not None:
                    pending.append(item[2])
                if key in hosts and hosts[key] in members:
                    pending.append(hosts[key])
            return False

        pieces = tuple(piece for piece in self.pieces if within(piece.window))
        if window not in members or not pieces or not any(piece.scope_confirmed for piece in pieces):
            return None
        membership = tuple(item for item in self.membership if item[0] == window or within(item[0]))
        if pieces == self.pieces and membership == self.membership:
            return self
        return replace(self, pieces=pieces, membership=membership, source_canvas=self.source_canvas or self)

    def hit(self, point: Point, *, scope_required: bool = True) -> tuple[ImagePiece, Point]:
        """Map delivered pixels; global locators need geometry, not object eligibility."""
        if scope_required and self.unavailable_mapping:
            raise local_failure(
                "the partial image has no confirmed overall window mapping",
                "snapshot_expired",
            )
        if self.delivered_coordinates is not None:
            point = self.delivered_coordinates.source_point(point)
        for piece in reversed(self.pieces):
            x, y, width, height = piece.box
            if x <= point[0] < x + width and y <= point[1] < y + height:
                if scope_required and not piece.scope_confirmed:
                    raise local_failure("the captured window's current scope is unconfirmed", "observation_incomplete")
                metadata = capture_metadata(piece.image.image)
                mapped = ((point[0] - x) * metadata["width"] / width, (point[1] - y) * metadata["height"] / height)
                if scope_required:
                    with Image.open(BytesIO(piece.image.image)) as pixels:
                        if pixels.convert("RGBA").getpixel((math.floor(mapped[0]), math.floor(mapped[1])))[3] == 0:
                            raise local_failure("this point is outside confirmed visible window pixels")
                return piece, mapped
        if self.source_canvas is not None and len(self.pieces) < len(self.source_canvas.pieces):
            # A fixed view retains the original RGBA composite. Visible
            # pixels outside its retained pieces still have no input authority.
            x, y = point
            with Image.open(BytesIO(self.png)) as pixels:
                if (
                    0 <= x < pixels.width and 0 <= y < pixels.height
                    and pixels.getpixel((math.floor(x), math.floor(y)))[3] != 0
                ):
                    raise local_failure("the point is outside the selected window's captured scope")
        raise local_failure(
            "the point is in a gap outside the captured windows",
        )


def signature(members: Mapping[str, Mapping[str, Any]]) -> Membership:
    return tuple(
        sorted(
            (item["window"], item["surface"], item["parent"], item["parentState"], item["modal"])
            for item in members.values()
        )
    )


def content_signature(members: Mapping[str, Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    """Freeze logical host evidence with the exact captured canvas."""
    return tuple(sorted(
        (key, item["content"]["host"])
        for key, item in members.items()
        if item.get("content", {}).get("host") in members
    ))


def shared_pixel_signature(members):
    return tuple((key, host) for key, host in content_signature(members)
                 if members[key]["content"]["evidence"] == "nativeWindowID")


def frame(image: CapturedWindow) -> ImageBox:
    a, b, c, d, x, y = image.coordinates["toNative"]
    invalid_b = True if b != 0 or c != 0 or a <= 0 or d <= 0 else False
    if invalid_b:
        raise ValueError("The driver did not provide a rectangular, positive window mapping.")
    metadata = capture_metadata(image.image)
    return x, y, a * metadata["width"], d * metadata["height"]


def overlaps(a: ImageBox, b: ImageBox) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
