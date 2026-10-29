# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from computer_use.interaction.read.delivery.coverage import observation_coverage
from computer_use.interaction.read.presentation.rendering.formatting import bounded, field, quoted, render_tree
from computer_use.interaction.read.presentation.tree import unique

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.protocol.records import Record


def window_context(windows: Sequence[Record], current: str | None) -> list[str]:
    """Format the selected window and its relationship to the working root.

    Args:
        current: Currently confirmed target or catalogue; absence is not a guessed fallback.
    """
    lines, known = [], {w["ref"] for w in windows}
    if len(windows) > 1:
        lines.append(
            "Windows: "
            + "; ".join(w["ref"] + " " + quoted(bounded(w["title"] or "Untitled", 160)) for w in windows)
            + "; current="
            + (current if current is not None else "unknown")
        )
    if any("document" in w for w in windows):

        def _iter_w(_source_items: Iterator[Any]) -> Iterator[Any]:
            for w in _source_items:
                if "document" in w or any(
                    other is not w and other["title"] == w["title"] and "document" in other for other in windows
                ):
                    yield w

        relevant = list(_iter_w(iter(windows)))
        lines.append(
            "Window documents: "
            + "; ".join(
                w["ref"] + " " + ("unknown" if "document" not in w else quoted(w["document"])) for w in relevant
            )
        )
    relations = []
    for window in windows:
        facts, kind = [], window.get("kind", "window")
        if kind != "window":
            facts.append(field(kind))
        if window.get("onScreen") is False:
            facts.append("not on screen")
        if window.get("parent") and window["parent"] in known and window["parent"] != window["ref"]:
            facts.append(("modal for " if window.get("modal") else "belongs to ") + window["parent"])
        elif window.get("modal"):
            facts.append("modal; owner unknown")
        if window.get("modal") is False and kind in ("dialog", "panel", "sheet"):
            facts.append("non-modal")
        blocking = [child["ref"] for child in windows if child.get("modal") and child.get("parent") == window["ref"]]
        if blocking:
            facts.append("blocked by " + ", ".join(blocking))
        if facts:
            relations.append(window["ref"] + " [" + "; ".join(facts) + "]")
    if relations:
        lines.append("Window relations: " + "; ".join(relations))
    return lines


def render_observation(observation: Record, observed: Record | None = None) -> RenderedTree:
    """Format an application observation's scope, content, image, and collection gaps.

    Args:
        observed: Capture provenance or original observation used by the current stage.
    """
    rendered = render_tree(observation["root"], observation["context"]) if observation["root"] is not None else None
    lines = (
        rendered["text"].split("\n")
        if rendered is not None
        else [
            f"App: {field(observation['context'].get('app', 'Unknown'))}.",
            (f"Tree: unavailable ({field(observation.get('unavailable', 'no accessible tree'))})"),
        ]
    )
    if "title" in observation:
        lines[0] = (
            f"Window: "
            f"{quoted(bounded(observation['title'], 500))}"
            f", App: "
            f"{field(observation['context'].get('app', 'Unknown'))}"
            f"."
        )
    document = (
        (
            "Document: unknown (not reported)"
            if observation.get("document") is None
            else "Document: " + quoted(observation["document"])
        )
        if "document" in observation
        else None
    )
    image_context = "\n".join(
        ([lines[0]] if re.match("^(?:Window|App):", lines[0] if lines else "") else [])
        + ([document] if document is not None else [])
    )
    if document is not None:
        lines.insert(1, document)
    if "applicationMenu" in observation:
        windows = {w["ref"]: w for w in observation["windows"]}

        def label(ref: str | None) -> str:
            """Format a node caption used by the observation summary.

            Args:
                ref: Display reference from the observation, not an operating-system identifier.
            """
            if ref is None:
                return "unknown"
            title = windows.get(ref, {}).get("title")
            shown = len(windows) > 1 or (
                title is not None and lines[0].startswith("Window: " + quoted(bounded(title, 500)) + ", ")
            )
            return ref + (" " + quoted(bounded(title, 160)) if not shown and title is not None else "")

        def ancestry(ref: str | None) -> set[str]:
            """Collect the visible source ancestry for an observation reference.

            Args:
                ref: Display reference from the observation, not an operating-system identifier.
            """
            seen = set()
            while ref is not None and ref in windows and ref not in seen:
                seen.add(ref)
                ref = windows[ref].get("parent")
            return seen

        viewed, menu = ancestry(observation["current"]), observation["applicationMenu"]
        outside = unique(
            ref
            for ref in (menu["mainWindow"], menu["focusedWindow"])
            if ref is not None and observation["current"] is not None and not ancestry(ref).intersection(viewed)
        )
        lines.insert(
            1,
            (
                f"Application menu: app-wide; reported main="
                f"{label(menu['mainWindow'])}"
                f"; focused="
                f"{label(menu['focusedWindow'])}"
            )
            + ("; outside viewed scope: " + ", ".join(outside) if outside else "")
            + ".",
        )
    tree_at = next((i_value for i_value, line_value in enumerate(lines) if line_value.startswith("Tree:")), -1)
    screenshot = observation.get("screenshot")
    from computer_use.interaction.read.presentation.formatting import number_string

    caption = (
        (
            f"Screenshot: captured "
            f"{number_string(screenshot['width'])}"
            f"x"
            f"{number_string(screenshot['height'])}"
            f" px; print_image to view."
        )
        if screenshot is not None
        else (f"Screenshot: unavailable ({field(observation.get('screenshotError', 'no captured window'))})")
    )
    lines.insert(1 if tree_at < 0 else tree_at + 1, caption)
    if observation.get("overviewText"):
        at = 0 if tree_at < 0 else tree_at
        lines[at:at] = compact_overview(observation["overviewText"]).split("\n")
    if observed is not None:
        lines.insert(1, "Observation: " + quoted(observed["id"]))
    lines[:0] = window_context(observation["windows"], observation["current"])
    refs = rendered["refs"] if rendered is not None else []
    visible = set(refs)
    if "sharedFocus" in observation:
        related = []
        for ref in observation["sharedFocus"]["refs"]:
            if ref in visible:
                related.append(ref)
            else:
                owner = rendered["projection"]["foldedInto"].get(ref) if rendered is not None else None
                if owner and owner in visible:
                    related.append("inside " + owner)
        outside = observation["sharedFocus"]["outside"] + len(observation["sharedFocus"]["refs"]) - len(related)
        if outside:
            related.append(number_string(outside) + " outside the expanded tree")
        if related:
            lines.append("Shared focus: " + ", ".join(related) + " (app-declared keyboard relation; input unverified).")
    if "menuPath" in observation:
        lines.append(
            "[Menu path: "
            + " > ".join(quoted(v) for v in observation["menuPath"])
            + "; native data only, not opened/executed. Other UI omitted; read() for window.]"
        )
        if observation.get("commandRef") and observation["commandRef"] in visible:
            lines.append(
                (f"[Command: element({quoted(observation['commandRef'])}).click() when enabled and clickable.]")
            )
    for note in observation.get("notes", []):
        lines.append("[" + field(note) + "]")
    if observation["current"] is None and observation["windows"]:
        lines.append(('Choose a window with app.window("<ref>").read() to inspect its contents and screenshot.'))
    roots = rendered["roots"] if rendered is not None else []
    return dict(
        text="\n".join(lines),
        refs=refs,
        roots=roots,
        coverage=observation_coverage(observation, roots),
        contextRefs=rendered["contextRefs"] if rendered is not None else {},
        imageContext=image_context,
    )


def compact_overview(text: str) -> str:
    text = re.sub(
        (
            "^Overview: app layout ([0-9]+x[0-9]+) px; context only; use "
            "app\\.window\\(ref\\)\\.read\\(\\) for click "
            "coordinates\\.$"
        ),
        "Overview: \\1 px; context only (not click coordinates).",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    return text.replace(
        (
            "Rectangles are full window bounds in overview pixels and may "
            "extend outside "
            "the image. Display coverage excludes screen gaps; it does not prove "
            "visibility through overlapping "
            "windows."
        ),
        (
            "Overview bounds: full windows in overview pixels, possibly "
            "outside image; "
            "display coverage excludes gaps, not "
            "occlusion."
        ),
        1,
    )
