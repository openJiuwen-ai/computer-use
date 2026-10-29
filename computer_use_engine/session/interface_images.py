# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured window composition and coordinate mapping for each UI view."""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Protocol, TypeAlias

from computer_use.diagnostics import local_failure
from computer_use.protocol.captures import capture_metadata
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from computer_use.native.contracts import Binding
    from computer_use.protocol.png import PNG
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
    """

    window: str
    image: CapturedWindow
    box: ImageBox


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

    def hit(self, point: Point) -> tuple[ImagePiece, Point]:
        if self.unavailable_mapping:
            raise local_failure(
                "the partial image has no confirmed overall window mapping",
                'read a specific member with app.window(ref).read(view="image")',
                "snapshot_expired",
            )
        for piece in reversed(self.pieces):
            x, y, width, height = piece.box
            if x <= point[0] < x + width and y <= point[1] < y + height:
                metadata = capture_metadata(piece.image.image)
                mapped = ((point[0] - x) * metadata["width"] / width, (point[1] - y) * metadata["height"] / height)
                with Image.open(BytesIO(piece.image.image)) as pixels:
                    if pixels.convert("RGBA").getpixel((math.floor(mapped[0]), math.floor(mapped[1])))[3] == 0:
                        raise local_failure(
                            "this point is outside confirmed visible window pixels",
                            "choose a visible control or read a specific Window",
                        )
                return piece, mapped
        raise local_failure(
            "the point is in a gap outside the captured windows",
            "choose a visible window control; canvas gaps are not desktop targets",
        )


def signature(members: Mapping[str, Mapping[str, Any]]) -> Membership:
    return tuple(
        sorted(
            (item["window"], item["surface"], item["parent"], item["parentState"], item["modal"])
            for item in members.values()
        )
    )


def frame(image: CapturedWindow) -> ImageBox:
    a, b, c, d, x, y = image.coordinates["toNative"]
    invalid_b = True if b != 0 or c != 0 or a <= 0 or d <= 0 else False
    if invalid_b:
        raise ValueError("The driver did not provide a rectangular, positive window mapping.")
    metadata = capture_metadata(image.image)
    return x, y, a * metadata["width"], d * metadata["height"]


def overlaps(a: ImageBox, b: ImageBox) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
