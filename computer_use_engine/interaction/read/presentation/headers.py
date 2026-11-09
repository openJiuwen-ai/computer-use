# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured interface headers and their terminal text/detail representations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import Literal

from computer_use.interaction.read.delivery.image_caption import window_state_notes
from computer_use.interaction.read.presentation.formatting import json_string
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.json import dumps


@dataclass(frozen=True)
class MemberHeading:
    """One real member window, its display scope, and original directory sources.

    Attributes:
        reported_input: Whether the directory reports input in this window; it
            does not confirm the keyboard receiver.
        source_directory: Directory captured before this member's component read.
        rechecked_directory: Directory captured after that read.
    """

    ref: int
    title: str
    native_window: str
    main: bool
    current: bool
    reported_input: bool
    parent: int | None
    parent_state: Literal["known", "none", "unknown"]
    modal: bool | None
    in_scope: bool
    expanded: bool
    target: Mapping[str, object] | None = None
    source_target: Mapping[str, object] | None = None
    source_applications: Mapping[str, object] | None = None
    source_directory: Mapping[str, object] | None = None
    rechecked_directory: Mapping[str, object] | None = None
    scope_confirmed: bool | None = None
    content_kind: str = "window"
    content_host: int | None = None

    def __post_init__(self) -> None:
        for name in ("target", "source_target", "source_applications", "source_directory", "rechecked_directory"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class InterfaceHeader:
    """Freeze one App/window observation's scope and complete directory metadata.

    Window scope includes the fixed root and its confirmed descendants. Current
    and reported input retain their captured knowledge independently of bodies.
    """

    application: str
    scope: Literal["application", "window"]
    scope_root: int | None
    current: int | None
    reported_input: int | None
    input_state: Literal["member", "outside", "unknown"]
    windows: tuple[MemberHeading, ...]
    directory_gaps: tuple[str, ...]
    directory: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "directory", freeze(self.directory))

    @property
    def scoped(self) -> tuple[MemberHeading, ...]:
        """Return the already confirmed windows within this read's actual range."""
        return tuple(window for window in self.windows if window.in_scope)


@dataclass(frozen=True)
class ImageHeader:
    """Retain delivered image scope and exact capture/composition metadata.

    Captures distinguish collected images from the sources actually displayed.
    Partial canvases keep their selected source even when they have no pieces.
    """

    kind: Literal["single", "composite", "partial", "unavailable"]
    windows: tuple[int, ...]
    mapping_available: bool
    captures: tuple[Mapping[str, object], ...]
    canvas: Mapping[str, object] | None
    pieces: tuple[tuple[str, tuple[float, float, float, float]], ...] = ()
    origin: tuple[float, float] | None = None
    scale: float | None = None
    membership: tuple[tuple[str, str, str | None, str, bool | None], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "captures", tuple(freeze(item) for item in self.captures))
        object.__setattr__(self, "canvas", freeze(self.canvas))


def _quoted(value: str) -> str:
    return json_string(value)


def _flags(window: MemberHeading) -> list[str]:
    flags: list[str] = []
    if window.main:
        flags.append("main")
    if window.current:
        flags.append("current")
    if window.reported_input:
        flags.append("reports input")
    if window.modal is True:
        flags.append("modal")
    elif window.modal is None:
        flags.append("modal unknown")
    if window.parent_state == "known":
        flags.append(f"parent={window.parent}" if window.parent is not None else "parent outside catalogue")
    elif window.parent_state == "unknown":
        flags.append("parent unknown")
    return flags


def member_heading(window: MemberHeading, *, name_in_body: bool = False) -> str:
    """Name a window and its relationships, reusing a proved full-body Name when present."""
    flags = _flags(window)
    if not window.in_scope:
        flags.append("App-level supplement" if window.expanded else "outside read scope")
    elif not window.expanded:
        flags.append("not expanded")
    suffix = " (" + ", ".join(flags) + ")" if flags else ""
    title = "" if name_in_body else " " + _quoted(window.title)
    if window.content_host is not None:
        label = "Menu" if window.content_kind == "menu" else "Popup" if window.content_kind == "popup" else "Content"
        return f"{label} for window={window.content_host}:" + title + suffix
    if window.content_kind in ("menu", "popup"):
        return f"{window.content_kind.title()} source {window.ref}:" + title + suffix + f"; window={window.ref}"
    if window.content_kind == "application":
        return f"Application content source {window.ref}:" + title + suffix
    if window.content_kind == "unknown":
        return f"Interface source {window.ref}:" + title + suffix + f"; window={window.ref} (unclassified)"
    return f"Window {window.ref}:" + title + suffix + f"; window={window.ref}"


def reported_input_label(ref: int | None, *, observed: bool = True) -> str:
    """Describe the captured focus report independently of future input preparation.

    Args:
        ref: Published member window number, or None when the reported window is
            outside the selected instance.
        observed: Whether this observation supplied a window focus report.

    Returns:
        The reported member number, an outside-instance report, or unknown.
    """
    if not observed:
        return "unknown"
    return str(ref) if ref is not None else "outside this observation (observed focus)"


def interface_header(header: InterfaceHeader, *, name_in_body: bool = False) -> str:
    """Render actual scope and uncertainty, optionally reusing a proved single-window Name."""
    lines: list[str] = []
    single = header.scoped[0] if len(header.scoped) == 1 and header.scoped[0].content_kind == "window" else None
    if single is not None:
        suffix = " (window scope)" if header.scope == "window" else ""
        title = "" if name_in_body else ": " + _quoted(single.title)
        heading = f"Window {single.ref}{title}, App: {_quoted(header.application)}{suffix}"
        if header.directory.get("strictWindow"):
            lines.append(heading + ". Reuse the existing App handle for this exact window.")
        else:
            lines.append(heading + f"; computer.app({header.directory['application']!r}).read(window={single.ref}).")
        relations = [flag for flag in _flags(single) if flag not in ("main", "current", "reports input")]
        if relations:
            lines.append("Window relation: " + ", ".join(relations))
    else:
        lines.append(f"App: {_quoted(header.application)}.")
        if header.scope == "window":
            scope = "" if header.directory.get("strictWindow") else " and confirmed descendants"
            lines.append(f"Scope: Window {header.scope_root}{scope}")
    if header.current is None:
        lines.append("Current window: unknown")
    elif single is not None and header.current != single.ref:
        lines.append(f"Detailed observation: Window {header.current}; window={header.current}")
    if header.input_state != "member":
        lines.append(
            "Reported input window: "
            + reported_input_label(header.reported_input, observed=header.input_state != "unknown")
        )
    elif single is not None and (header.current is None or header.reported_input != header.current):
        lines.append("Reported input window: " + reported_input_label(header.reported_input))
    for window in header.windows:
        if not window.in_scope or not window.expanded:
            lines.append(member_heading(window))
    lines.extend("Directory partial: " + reason for reason in header.directory_gaps)
    return "\n".join(lines)


def image_state_notes(header: ImageHeader) -> tuple[str, ...]:
    """Report independent capture-time state for the windows that supplied pixels."""
    notes = []
    for source in header.captures:
        if source.get("displayed") is not True or source.get("ref") not in header.windows:
            continue
        capture = source.get("capture")
        if isinstance(capture, Mapping):
            notes.extend(f"Image note (Window {source['ref']}): {note}" for note in window_state_notes(capture))
    return tuple(notes)


def image_header(header: ImageHeader) -> str:
    """Identify complete image coordinates and report the displayed sources' state."""
    lines = []
    if header.kind not in ("partial", "unavailable"):
        label = "Window " if len(header.windows) == 1 else "windows "
        lines.append("Image: " + label + ", ".join(map(str, header.windows)))
    lines.extend(image_state_notes(header))
    return "\n".join(lines)


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def captured_metadata(header: InterfaceHeader | ImageHeader) -> str:
    """Expose all frozen metadata through the same observation's global details document."""
    label = "interface" if isinstance(header, InterfaceHeader) else "image"
    return f"Captured {label} metadata: " + dumps(_plain(header)).replace("\x85", "\\u0085").replace(
        "\u2028", "\\u2028"
    ).replace("\u2029", "\\u2029")
