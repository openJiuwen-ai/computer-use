# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build frame-scoped visual references from captured OCR evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from computer_use.protocol.captures import capture_metadata
from computer_use.protocol.png import png_size

from .model import VisualElement, VisualIndex

DEFAULT_MAX_LINES = 64


def visual_frame_id(png) -> str:
    """Return the capture identity that owns this exact image, or an empty string."""
    metadata = capture_metadata(png)
    return str(metadata["id"]) if metadata is not None else ""


def build_visual_index(
    png,
    *,
    frame_id: str | None = None,
    tree_text: str = "",
    metadata: Mapping[str, Any] | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> VisualIndex | None:
    """Turn captured OCR lines into visual-only references.

    Args:
        png: Delivered PNG whose ``visualText`` metadata supplies the regions.
        frame_id: Capture identity; derived from registered metadata when omitted.
        tree_text: Already formatted accessibility text used for exact-name deduplication.
        metadata: Optional capture metadata for tests or callers that already hold it.
        max_lines: Maximum OCR lines considered for this index.

    Returns:
        A visual index, or None when the image has no usable OCR evidence.
    """
    if max_lines < 1:
        raise ValueError("max_lines must be positive")
    evidence = capture_metadata(png) if metadata is None else metadata
    if evidence is None:
        return None
    visual = evidence.get("visualText")
    if not isinstance(visual, Mapping) or visual.get("status") == "unavailable":
        return None

    size = png_size(png)
    width, height = float(size["width"]), float(size["height"])
    identity = frame_id if frame_id is not None else str(evidence.get("id") or "")
    lines = visual.get("lines") or ()
    elements: list[VisualElement] = []
    seen: set[tuple[str, float, float]] = set()

    for line in lines[:max_lines]:
        if not isinstance(line, Mapping):
            continue
        label = _normalize_label(str(line.get("text") or ""))
        if not label or _covered_by_tree(label, tree_text):
            continue
        rect = line.get("rect") or {}
        try:
            x = float(rect["x"])
            y = float(rect["y"])
            item_width = float(rect["width"])
            item_height = float(rect["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if item_width <= 0 or item_height <= 0:
            continue
        if x < 0 or y < 0:
            continue
        if x + item_width > width + 1e-6:
            continue
        if y + item_height > height + 1e-6:
            continue
        key = (label, round(x, 3), round(y, 3))
        if key in seen:
            continue
        seen.add(key)
        elements.append(
            VisualElement(
                ref=len(elements) + 1,
                label=label,
                rect=(x, y, item_width, item_height),
                source=visual.get("source", "windowsOCR"),
                frame_id=identity,
            )
        )

    if not elements:
        return None
    return VisualIndex(
        frame_id=identity,
        elements=tuple(elements),
        truncated=bool(visual.get("truncated")) or len(lines) > max_lines,
        source=visual.get("source", "windowsOCR"),
    )


_CJK_GAP = re.compile(r"(?<=[㐀-䶿一-鿿豈-﫿])\s+(?=[㐀-䶿一-鿿豈-﫿])")


def _normalize_label(text: str) -> str:
    """Collapse OCR whitespace and the CJK gaps providers often insert."""
    return _CJK_GAP.sub("", " ".join(text.split()))


def _covered_by_tree(label: str, tree_text: str) -> bool:
    """Skip text already named by an addressable accessibility row.

    Only the row declaration counts; captured document prose does not. Windows
    trees often render names without quotes, so exact quoted matching is
    insufficient. Require a token boundary for multi-character labels while
    keeping one-character OCR digits available for image-only controls.
    """
    if not tree_text:
        return False
    # A document body is prose, not a row name. Chromium accessibility bridges
    # can flatten an entire editor into one Document text field; dropping OCR
    # rows because that prose mentions the label removes the only usable
    # reference for controls the bridge does not expose separately.
    rows = [line.split("Document text:", 1)[0] for line in tree_text.splitlines()]
    if any(f'"{label}"' in row or f"'{label}'" in row for row in rows):
        return True
    if len(label) < 2:
        return False
    boundary = r"[A-Za-z0-9_㐀-䶿一-鿿豈-﫿]"
    pattern = rf"(?<!{boundary}){re.escape(label)}(?!{boundary})"
    return any(re.search(pattern, row) is not None for row in rows)
