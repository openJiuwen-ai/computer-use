# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fit encoded model images to the delivery budget and retain their inverse."""

import base64
import math
from dataclasses import dataclass, replace
from io import BytesIO

from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.protocol.png import decode_png, png_size
from computer_use.session.image_coordinates import ImageCoordinates
from PIL import Image


@dataclass(frozen=True)
class ImageDelivery:
    """One output PNG and the exact source image whose coordinates it represents."""

    source: bytes | bytearray | memoryview
    png: bytes
    coordinates: ImageCoordinates


def project_document(document, coordinates: ImageCoordinates):
    """Project only image-space presentation facts; preserve source identity.

    Desktop facts are structured all the way to this delivery boundary. Never
    infer coordinates from rendered strings or rewrite the native catalogue.
    Window source captures, AX screen geometry and preview grids are distinct
    spaces and are deliberately not transformed here.
    """
    if not isinstance(document, DisplayDocument):
        return document

    def project(value):
        if isinstance(value, DisplayText):
            if value.kind == "desktop":
                desktop, *references = value.values
                canvas = desktop["canvas"]
                if (canvas["width"], canvas["height"]) != coordinates.source_size:
                    raise ValueError("Desktop presentation does not belong to the delivered source image.")
                projected = dict(
                    desktop,
                    canvas=dict(width=coordinates.delivered_size[0], height=coordinates.delivered_size[1]),
                    displays=[
                        dict(item, rect=coordinates.delivered_rect(item["rect"])) for item in desktop["displays"]
                    ],
                    windows=[dict(item, rect=coordinates.delivered_rect(item["rect"])) for item in desktop["windows"]],
                )
                return replace(value, values=(freeze(projected), *references))
            return replace(value, values=tuple(project(item) for item in value.values))
        if isinstance(value, tuple):
            return tuple(project(item) for item in value)
        return value

    return replace(document, parts=tuple(project(part) for part in document.parts))


def prepare_image(
    png: bytes | bytearray | memoryview, *, max_dimension: int = 2048, max_pixels: int = 1536 * 1536
) -> ImageDelivery:
    """Resize once before publication; never derive a map from a nominal ratio."""
    valid_dimension = type(max_dimension) is int and max_dimension > 0
    if not valid_dimension or type(max_pixels) is not int or max_pixels <= 0:
        raise ValueError("Image delivery limits must be positive integers.")
    decode_png(base64.b64encode(png).decode("ascii"))
    size = png_size(png)
    width, height = size["width"], size["height"]
    scale = min(1, max_dimension / max(width, height), math.sqrt(max_pixels / (width * height)))
    fitted = max(1, math.floor(width * scale)), max(1, math.floor(height * scale))
    if fitted == (width, height):
        encoded = memoryview(png).tobytes()
    else:
        with (
            Image.open(BytesIO(png)) as source,
            source.convert("RGBA") as rgba,
            rgba.resize(fitted, Image.Resampling.LANCZOS) as image,
        ):
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            encoded = buffer.getvalue()
    actual = png_size(encoded)
    return ImageDelivery(png, encoded, ImageCoordinates((width, height), (actual["width"], actual["height"])))
