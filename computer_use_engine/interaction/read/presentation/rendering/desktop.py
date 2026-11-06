# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.image_caption import IMAGE_COORDINATE_SPACE
from computer_use.interaction.read.presentation.formatting import json_string, number_string

if TYPE_CHECKING:
    from computer_use.protocol.records import Record


def render_desktop(
    value: Record,
    applications: Mapping[str, int] | None = None,
    windows: Mapping[str, int] | None = None,
) -> str:
    "Format desktop displays, capture geometry, and observation provenance."

    def box(rect: Record) -> str:
        return (
            "("
            + ", ".join(number_string(math.floor(rect[k] * 100 + 0.5) / 100) for k in ("x", "y", "width", "height"))
            + ")"
        )

    def application(selector: str | None, separator: str) -> str:
        if not selector:
            return ""
        if applications is None:
            return separator + "app=" + json_string(selector)
        ref = applications.get(selector)
        return separator + f"computer.app({ref})" if ref is not None else ""

    captured = sum(bool(d["captured"]) for d in value["displays"])
    front = value["frontmost"]
    lines = [
        "Desktop: full view",
        "Foreground: "
        + (json_string(front["name"]) + application(front.get("selector"), "; ") if front is not None else "unknown"),
        (
            f"Screenshot: desktop "
            f"{number_string(value['canvas']['width'])}"
            f"x"
            f"{number_string(value['canvas']['height'])}"
            f" px; origin=top-left"
        )
        if captured
        else "Screenshot: unavailable; window catalogue only",
        "Displays (x,y,width,height in desktop PNG):",
    ]
    for d in value["displays"]:
        lines.append(
            f"  {json_string(d['name'])} {box(d['rect'])} "
            + ("captured" if d["captured"] else "unavailable: " + json_string(d["error"]))
        )
    if captured:
        lines.append(IMAGE_COORDINATE_SPACE)
        lines.append("Display gaps and uncaptured regions are blank canvas, not interactive desktop.")
    lines.append("On-screen windows (rectangles may overlap; not element references):")
    for index, window in enumerate(value["windows"]):
        reference = windows.get(str(index)) if windows is not None else None
        lines.append(
            "  "
            + json_string(window["app"])
            + " — "
            + (json_string(window["title"]) if window["title"] else "<untitled>")
            + " "
            + box(window["rect"])
            + (f" window={reference}" if reference is not None else "")
            + application(window.get("selector"), "; application: ")
        )
    if not value["windows"]:
        lines.append("  Unknown" if value.get("catalogError") else "  None reported")
    if value["omittedWindows"]:
        lines.append(
            (f"Window catalogue truncated: {number_string(value['omittedWindows'])} additional windows omitted.")
        )
    if value.get("catalogError"):
        lines.append("Catalogue: " + json_string(value["catalogError"]))
    if captured:
        lines.append(
            "Use the point constructor shown beside this image for its whole canvas. "
            "Window catalogue and display images were collected sequentially; re-observe if they "
            "disagree."
        )
    return "\n".join(lines)
