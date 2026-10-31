# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Represent native heading ranges without absorbing separate window controls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HeadingSection:
    """A complete heading-led source sequence with any native heading subsections."""

    heading: str
    children: tuple[str | HeadingSection, ...]


@dataclass
class _OpenSection:
    level: int
    children: list[str | HeadingSection] = field(default_factory=list)


_CHROME = frozenset(("toolbar", "menuBar", "closeButton", "zoomButton", "minimizeButton", "window", "sheet"))
_WINDOW_BUTTONS = frozenset(("close", "minimize", "zoom", "fullscreen"))


def heading_sections(refs: Sequence[str], nodes: Mapping[str, Mapping[str, Any]]) -> tuple[str | HeadingSection, ...]:
    """Group heading ranges, respecting complete heading levels and chrome boundaries.

    A single heading with following content can introduce a region. Nesting uses
    native positive integer levels only when every heading supplies one; otherwise
    headings remain peers. A menu, separate window, modal or window control ends
    the current section and keeps its existing placement.
    """
    headings = [ref for ref in refs if nodes[ref]["role"] == "heading"]
    levels_known = bool(headings) and all(
        type(nodes[ref].get("headingLevel")) is int and nodes[ref]["headingLevel"] > 0 for ref in headings
    )
    stack = [_OpenSection(-1)]

    def close() -> None:
        completed = stack.pop()
        if len(completed.children) > 1:
            heading = completed.children[0]
            assert isinstance(heading, str)
            stack[-1].children.append(HeadingSection(heading, tuple(completed.children)))
        else:
            stack[-1].children.extend(completed.children)

    for ref in refs:
        node = nodes[ref]
        if (
            node["role"] in _CHROME
            or node["role"] == "button"
            and node.get("subrole") in _WINDOW_BUTTONS
            or node.get("modal") is True
            or node.get("subrole") in ("dialog", "alert", "systemDialog", "popover")
        ):
            while len(stack) > 1:
                close()
            stack[0].children.append(ref)
            continue
        if node["role"] == "heading":
            level = node["headingLevel"] if levels_known else 0
            while len(stack) > 1 and stack[-1].level >= level:
                close()
            stack.append(_OpenSection(level, [ref]))
        else:
            stack[-1].children.append(ref)
    while len(stack) > 1:
        close()
    return tuple(stack[0].children)
