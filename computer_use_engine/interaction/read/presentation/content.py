# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured text fields; formatting is evaluated only at a rendering boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.ir import freeze

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.interaction.read.presentation.records import RenderedTree


@dataclass(frozen=True)
class DisplayText:
    """A captured value or explicit formatting operation in the display document.

    Attributes:
        kind: Operation interpreted by the terminal text renderer.
        values: Frozen input values, nested text fields, or a format template and its inputs.
    """

    kind: Literal[
        "literal",
        "template",
        "quoted",
        "json",
        "field",
        "number",
        "join",
        "indent",
        "details_hint",
        "desktop",
        "interface_header",
        "member_heading",
        "image_header",
        "captured_metadata",
        "capture_status",
    ]
    values: tuple[object, ...]


def text(value: str) -> DisplayText:
    """Retain captured text or a fixed label without formatting it."""
    return DisplayText("literal", (value,))


def template(pattern: str, *values: object) -> DisplayText:
    """Retain a format template and its original input values."""
    return DisplayText("template", (pattern, *(freeze(value) for value in values)))


def quoted(value: str) -> DisplayText:
    """Request JSON quoting of a captured value at final rendering."""
    return DisplayText("quoted", (value,))


def json_value(value: str | DisplayText) -> DisplayText:
    """Request the observation header's original JSON string representation."""
    return DisplayText("json", (value,))


def field(value: str) -> DisplayText:
    """Request the renderer's one-line field representation."""
    return DisplayText("field", (value,))


def number(value: int | float) -> DisplayText:
    """Request the shared platform-independent number representation."""
    return DisplayText("number", (value,))


def join(parts: Iterable[str | DisplayText], separator: str = "\n") -> DisplayText:
    """Compose text fields while preserving their individual structured inputs."""
    return DisplayText("join", (separator, tuple(text(part) if isinstance(part, str) else part for part in parts)))


def live_subtree_hint(roots: Iterable[str]) -> str:
    """Point to bounded local detail pages when any source has captured supplements."""
    distinct = tuple(dict.fromkeys(roots))
    from computer_use.interaction.read.presentation.details import DETAILS_HINT

    return "\n" + DETAILS_HINT if distinct else ""


def omit_public_details_hint(prepared: PreparedSnapshot) -> bool:
    """Use the Skill's detail-page instructions for ordinary compact whole views.

    Explicit subtrees and diagnostic formatters retain their local instruction.
    The typed source remains in the document and still supplies captured details.
    """
    request = getattr(prepared, "request", None)
    return bool(
        request is not None
        and request.compact
        and request.public_api
        and request.share_descriptions
        and all(root.source is not None and not root.source.context.get("subtreeRef") for root in prepared.roots)
    )


def render_text(value: DisplayText | str, *, snapshots: Mapping[int, RenderedTree] | None = None) -> str:
    """Render a structured field as part of the final output operation."""
    if isinstance(value, str):
        return value
    from computer_use.interaction.read.presentation.formatting import json_string, number_string
    from computer_use.interaction.read.presentation.rendering.formatting import (
        field as format_field,
    )
    from computer_use.interaction.read.presentation.rendering.formatting import (
        quoted as format_quoted,
    )

    kind, args = value.kind, value.values
    if kind == "literal":
        return args[0]
    if kind == "template":
        return args[0].format(
            *(render_text(item, snapshots=snapshots) if isinstance(item, DisplayText) else item for item in args[1:])
        )
    if kind == "quoted":
        return format_quoted(args[0])
    if kind == "json":
        value = render_text(args[0], snapshots=snapshots) if isinstance(args[0], DisplayText) else args[0]
        return json_string(value)
    if kind == "field":
        return format_field(args[0])
    if kind == "number":
        return number_string(args[0])
    if kind == "join":
        return args[0].join(render_text(item, snapshots=snapshots) for item in args[1])
    if kind == "indent":
        return "\n".join("\t" + line for line in render_text(args[0], snapshots=snapshots).splitlines())
    if kind == "desktop":
        from computer_use.interaction.read.presentation.rendering.desktop import render_desktop

        return render_desktop(*args)
    if kind == "capture_status":
        return f"Capture status (system sharing indicator, Window {args[0].window}):\n"
    if kind in ("interface_header", "member_heading", "image_header", "captured_metadata"):
        from computer_use.interaction.read.presentation import headers

        return getattr(headers, kind)(args[0])
    if kind == "details_hint":
        if snapshots is None or id(args[0]) not in snapshots:
            raise ValueError("A details hint requires its already-rendered snapshot source")
        if omit_public_details_hint(args[0]):
            return ""
        rendered = snapshots[id(args[0])]
        return live_subtree_hint((str(args[1]),)) if rendered["details"] != rendered["text"] else ""
    raise ValueError("Unknown structured text operation: " + kind)
