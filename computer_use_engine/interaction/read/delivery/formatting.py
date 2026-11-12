# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from computer_use.interaction.read.presentation.formatting import bounded, field, quoted

if TYPE_CHECKING:
    from typing import Sequence

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
