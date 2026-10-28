from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.formatting import json_string, number_string

if TYPE_CHECKING:
    from typing import Any, Mapping


def png_summary(width: int, height: int, image_bytes: int) -> str:
    return (
        f"PNG image ("
        f"{number_string(width)}"
        f"x"
        f"{number_string(height)}"
        f" px; "
        f"{number_string(image_bytes)}"
        f" bytes; use print_image(...) to display)"
    )


def window_state_notes(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    "Sample facts and conditional guidance; never a pixel freshness verdict."
    state = metadata.get("windowState") if metadata is not None else None
    if state is None:
        return ()

    def label(value: Any) -> str:
        return "unknown" if value is None else ("true" if value else "false")

    notes = [
        f"Window state (independent samples during capture): "
        f"onScreen={label(state['onScreen'])}; ownerForeground={label(state['ownerForeground'])}. "
        "These independent samples do not measure occlusion or repaint."
    ]
    if state["onScreen"] is False or state["ownerForeground"] is False:
        notes.append(
            "Off-screen or background window pixels may lag accessibility. Capture time is not draw time; "
            "do not replay input based only on old pixels."
        )
    return tuple(notes)


def window_state_summary(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    notes = window_state_notes(metadata)
    return notes[:1] + (
        ("Off-screen window or background owner; pixels may lag accessibility.",) if len(notes) > 1 else ()
    )


def render_image_caption(width: int, height: int, facts: Mapping[str, Any]) -> str:
    return (
        _base_caption(width, height, facts)
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
        "(x, y, width, height). These are visual hints, not UIA elements or proof of clickability; "
        "recognition may be wrong."
    ]
    lines.extend(
        "\t" + json_string(line["text"]) + " " + str(tuple(line["rect"][k] for k in ("x", "y", "width", "height")))
        for line in visual["lines"]
    )
    if visual["truncated"] or visual["status"] == "partial":
        lines.append("Visual text is truncated; the image may contain other text.")
    return "\n".join(lines)


def _base_caption(width: int, height: int, facts: Mapping[str, Any]) -> str:
    """Format the captured window image's base dimensions and coordinate space.

    Args:
        facts: Native evidence used by the stage; observation is not input authority.
    """
    size = number_string(width) + "x" + number_string(height)
    kind = facts["kind"]
    if kind == "unknown":
        return f"Image: {size} px; source unknown; do not infer App.click coordinates."
    if kind == "desktop":
        return f"Image: desktop {size} px; not valid for App.click coordinates." + (
            " This image is stale; read the desktop again."
            if facts.get("stale")
            else " Origin=top-left; use its display layout and window catalogue."
        )
    if kind == "overview":
        return (
            f"Image: app overview {size} px; context only, not App.click coordinates; use app.window(ref).read().\n"
        ) + (facts.get("context") or "")
    validity = (
        ("This image is stale; read this App and return its current screenshot before using coordinates.")
        if facts.get("stale")
        else "Window PNG coordinates; printing does not refresh."
    )
    return (
        f"Image: window {size} px; origin=top-left\n{facts['identity']}"
        + ("" if facts.get("omitContext") else "\n" + facts["context"])
        + "\n"
        + validity
    )
