# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.formatting import json_string, number_string

IMAGE_COORDINATE_SPACE = (
    "Coordinate space: displayed image pixels. Origin: top-left; x increases right; y increases down."
)
IMAGE_POINT_GUIDANCE = "Use (x, y) as shown. The SDK handles resizing and screen/window offsets."

if TYPE_CHECKING:
    from typing import Any, Mapping


def png_summary(width: int, height: int, image_bytes: int) -> str:
    return f"PNG image ({number_string(width)}x{number_string(height)} px; {number_string(image_bytes)} bytes)"


def window_state_notes(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    "Window state sampled during capture."
    state = metadata.get("windowState") if metadata is not None else None
    if state is None:
        return ()

    def label(value: Any) -> str:
        return "unknown" if value is None else ("true" if value else "false")

    return (
        f"Window state (independent samples during capture): "
        f"onScreen={label(state['onScreen'])}; ownerForeground={label(state['ownerForeground'])}.",
    )


def window_state_summary(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    return window_state_notes(metadata)


def render_image_caption(width: int, height: int, facts: Mapping[str, Any]) -> str:
    return (
        _base_caption(width, height, facts)
        + ("\nCapture: " + json_string(facts["capture"]) if facts.get("capture") else "")
        + "".join("\n" + note for note in facts.get("captureState", ()))
        + "".join("\nImage note: " + json_string(reason) for reason in facts.get("stopReasons", ()))
        + _visual_text(facts.get("visualText"))
    )


def _visual_text(visual: Mapping[str, Any] | None) -> str:
    "Format Windows OCR text and image-pixel rectangles as visual observation hints."
    if visual is None:
        return ""
    if visual["status"] == "unavailable":
        return "\nVisual text (Windows OCR): unavailable. " + visual.get("reason", "")
    lines = [
        "\nVisual text (Windows OCR, " + str(visual["language"]) + "): recognized text and image-pixel rectangles "
        "(x, y, width, height)."
    ]
    lines.extend(
        "\t" + json_string(line["text"]) + " " + str(tuple(line["rect"][k] for k in ("x", "y", "width", "height")))
        for line in visual["lines"]
    )
    if visual["truncated"] or visual["status"] == "partial":
        lines.append("Visual text coverage: partial.")
    return "\n".join(lines)


def _base_caption(width: int, height: int, facts: Mapping[str, Any]) -> str:
    """Format the captured window image's base dimensions and coordinate space.

    Args:
        facts: Native evidence used by the stage; observation is not input authority.
    """
    size = number_string(width) + "x" + number_string(height)
    kind = facts["kind"]
    if kind == "unknown":
        return f"Image: {size} px; source unknown; coordinate mapping=unavailable.\n" + IMAGE_COORDINATE_SPACE
    if kind == "desktop":
        return (
            f"Image: desktop {size} px; origin=top-left\n"
            + IMAGE_COORDINATE_SPACE
            + ("\nCoordinate mapping: stale." if facts.get("stale") else "")
        )
    if kind == "overview":
        return (
            f"Image: app overview {size} px; coordinate mapping=unavailable.\n"
            + IMAGE_COORDINATE_SPACE
            + "\n"
            + (facts.get("context") or "")
        )
    validity = (
        "\nCoordinate mapping: stale."
        if facts.get("stale")
        else "\nCoordinate mapping: unavailable."
        if not facts.get("mappingAvailable", True)
        else ""
    )
    return (
        f"Image: window {size} px; origin=top-left\n{facts['identity']}"
        + ("" if facts.get("omitContext") else "\n" + facts["context"])
        + "\n"
        + IMAGE_COORDINATE_SPACE
        + validity
        + (" Coordinate target=" + facts["coordinateTarget"] + "." if facts.get("coordinateTarget") else "")
    )
