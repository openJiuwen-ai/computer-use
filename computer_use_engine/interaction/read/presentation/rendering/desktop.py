from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.formatting import jsonString, numberString

if TYPE_CHECKING:
    from computer_use.protocol.records import Record


def renderDesktop(value: Record, applications: Mapping[str, int] | None = None) -> str:
    "Format desktop displays, capture geometry, and observation provenance."

    def box(rect: Record) -> str:
        return (
            "("
            + ", ".join(numberString(math.floor(rect[k] * 100 + 0.5) / 100) for k in ("x", "y", "width", "height"))
            + ")"
        )

    def application(selector: str | None, separator: str) -> str:
        if not selector:
            return ""
        if applications is None:
            return separator + "app=" + jsonString(selector)
        ref = applications.get(selector)
        return separator + f"computer.app({ref})" if ref is not None else ""

    captured = sum(bool(d["captured"]) for d in value["displays"])
    front = value["frontmost"]
    lines = [
        f"Desktop: {value['mode']}; View: full",
        "Foreground: "
        + (
            jsonString(front["name"]) + application(front.get("selector"), "; ")
            if front is not None
            else "unknown"
        ),
        (
            f"Screenshot: desktop "
            f"{numberString(value['canvas']['width'])}"
            f"x"
            f"{numberString(value['canvas']['height'])}"
            f" px; origin=top-left"
        )
        if captured
        else "Screenshot: unavailable; window catalogue only",
        "Displays (x,y,width,height in desktop PNG):",
    ]
    for d in value["displays"]:
        lines.append(
            f"  {jsonString(d['name'])} {box(d['rect'])} "
            + ("captured" if d["captured"] else "unavailable: " + jsonString(d["error"]))
        )
    if captured:
        lines.append("Display gaps and uncaptured regions are blank canvas, not interactive desktop.")
    lines.append("On-screen windows (rectangles may overlap; not element references):")
    for window in value["windows"]:
        lines.append(
            "  "
            + jsonString(window["app"])
            + " — "
            + (jsonString(window["title"]) if window["title"] else "<untitled>")
            + " "
            + box(window["rect"])
            + application(window.get("selector"), " ")
        )
    if not value["windows"]:
        lines.append("  Unknown" if value.get("catalogError") else "  None reported")
    if value["omittedWindows"]:
        lines.append(
            (
                f"Window catalogue truncated: "
                f"{numberString(value['omittedWindows'])}"
                f" more windows; use the screenshot to inspect visible content."
            )
        )
    if value.get("catalogError"):
        lines.append("Catalogue: " + jsonString(value["catalogError"]))
    lines.append(
        (
            "Desktop pixels are not App.click coordinates. Window catalogue "
            "and display "
            "images were collected sequentially; re-observe if they "
            "disagree."
        )
    )
    return "\n".join(lines)
