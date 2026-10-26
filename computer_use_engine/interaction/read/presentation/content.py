"""Structured text fields; formatting is evaluated only at a rendering boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.read.presentation.ir import freeze

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree


@dataclass(frozen=True)
class DisplayText:
    """A captured value or explicit formatting operation in the display document.

    Attributes:
        kind: Operation interpreted by the terminal text renderer.
        values: Frozen input values, nested text fields, or a format template and its inputs.
    """

    kind: Literal[
        "literal", "template", "quoted", "json", "field", "number", "join", "indent", "details_hint", "desktop"
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


def render_text(value: DisplayText | str, *, snapshots: Mapping[int, RenderedTree] | None = None) -> str:
    """Render a structured field as part of the final output operation."""
    if isinstance(value, str):
        return value
    from computer_use.interaction.read.presentation.formatting import jsonString, numberString
    from computer_use.interaction.read.presentation.rendering.formatting import (
        field as format_field,
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
        return jsonString(value)
    if kind == "field":
        return format_field(args[0])
    if kind == "number":
        return numberString(args[0])
    if kind == "join":
        return args[0].join(render_text(item, snapshots=snapshots) for item in args[1])
    if kind == "indent":
        return "\n".join("\t" + line for line in render_text(args[0], snapshots=snapshots).splitlines())
    if kind == "desktop":
        from computer_use.interaction.read.presentation.rendering.desktop import renderDesktop

        return renderDesktop(args[0])
    if kind == "details_hint":
        if snapshots is None or id(args[0]) not in snapshots:
            raise ValueError("A details hint requires its already-rendered snapshot source")
        rendered = snapshots[id(args[0])]
        return (
            "\nAdditional native facts/actions/relations: (await app.element(" + str(args[1]) + ")).read()"
            if rendered["details"] != rendered["text"]
            else ""
        )
    raise ValueError("Unknown structured text operation: " + kind)
