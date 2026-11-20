# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Present visual references as a text legend and a display-only marked image."""

from __future__ import annotations

import re
from io import BytesIO
from typing import TYPE_CHECKING

from computer_use.protocol.png import PNG, png_size
from PIL import Image, ImageDraw, ImageFont

from .model import VisualIndex

if TYPE_CHECKING:
    from computer_use.session.image_coordinates import ImageCoordinates

MARK_COLOR = "#ff2fb3"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _legend_label(label: str) -> str:
    """Keep untrusted OCR text on one bounded, quote-safe legend line."""
    text = _CONTROL.sub(" ", label)
    text = " ".join(text.split()).replace('"', "'")
    return text[:200]


def render_legend(index: VisualIndex) -> str:
    """Render the reference table the model reads beside the image.

    Coordinates stay out of the legend: the marked image is the position
    evidence, and a resized delivery must not leave stale pixel numbers behind.
    """
    if not index.elements:
        return "Visual elements: unavailable. " + (index.detail or "no OCR text was captured.")
    lines = [
        "Visual elements (marked on the image):",
        'Use app.element("label") or app.element(VisualRef(n)); a new read invalidates visual targets.',
    ]
    for element in index.elements:
        lines.append(f'v{element.ref} "{_legend_label(element.label)}" source={element.source}')
    if index.truncated:
        lines.append("Visual element coverage: partial.")
    return "\n".join(lines)


def render_marks(
    png,
    index: VisualIndex,
    *,
    coordinates: ImageCoordinates | None = None,
) -> PNG | None:
    """Draw numbered outlines on a display-only copy of the delivered image.

    The returned image keeps the source dimensions unless ``coordinates`` supplies
    a delivered-size mapping for the same source PNG. It is never the coordinate
    authority for input. A display-only rendering failure returns None so an
    otherwise successful observation is not lost.
    """
    try:
        return _render_marked(png, index, coordinates)
    except (OSError, ValueError, MemoryError, Image.DecompressionBombError):
        return None


def _render_marked(png, index: VisualIndex, coordinates: ImageCoordinates | None) -> PNG:
    size = png_size(png)
    if coordinates is not None and tuple(coordinates.source_size) != (size["width"], size["height"]):
        # Never scale marks with a coordinate map from another frame or size.
        coordinates = None
    with Image.open(BytesIO(bytes(png))) as opened:
        image = opened.convert("RGBA")
    if coordinates is not None and coordinates.delivered_size != coordinates.source_size:
        image = image.resize(coordinates.delivered_size, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    font = _font()
    for element in index.elements:
        x, y, width, height = _delivered_rect(element.rect, coordinates)
        right, bottom = x + width, y + height
        draw.rectangle((x, y, right, bottom), outline=MARK_COLOR, width=2)
        badge_height = 16.0
        top = y - badge_height - 2.0
        if top < 0:
            top = bottom + 2.0
        if top + badge_height > image.height:
            top = max(0.0, y - badge_height - 2.0)
        badge_right = min(float(image.width), x + 30.0)
        if badge_right > x:
            draw.rectangle((x, top, badge_right, top + badge_height), fill=MARK_COLOR)
            draw.text((x + 3.0, top + 1.0), f"v{element.ref}", fill="white", font=font)
    output = BytesIO()
    image.save(output, format="PNG")
    return PNG(output.getvalue())


def _font():
    try:
        return ImageFont.load_default(size=14)
    except TypeError:
        return ImageFont.load_default()


def _delivered_rect(rect, coordinates: ImageCoordinates | None):
    if coordinates is None:
        return rect
    x, y, width, height = rect
    delivered = coordinates.delivered_rect({"x": x, "y": y, "width": width, "height": height})
    return delivered["x"], delivered["y"], delivered["width"], delivered["height"]
