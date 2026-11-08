# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Image presentation backed by the session's single provenance store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.image_caption import (
    IMAGE_POINT_GUIDANCE,
    png_summary,
    render_image_caption,
    window_state_notes,
)
from computer_use.interaction.read.presentation.formatting import json_string
from computer_use.protocol.png import png_size
from computer_use.session.images import (
    _source,
    image_delivered,
    image_delivery_epoch,
    image_delivery_required,
    image_metadata,
    require_image_delivery,
    reset_image_delivery,
)
from computer_use.session.images import (
    register_image as register_session_image,
)

__all__ = [
    "image_delivered",
    "image_delivery_epoch",
    "image_delivery_required",
    "image_label",
    "image_metadata",
    "register_image",
    "require_image_delivery",
    "reset_image_delivery",
]

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.content import DisplayText
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.session.image_coordinates import ImageCoordinates


def register_image(png, source):
    def summary(image):
        size = png_size(image)
        return png_summary(size["width"], size["height"], len(image))

    return register_session_image(png, source, summary)


def _context_text(value: str | DisplayDocument | DisplayText | None) -> str | None:
    """Render retained image context only when its caption is requested."""
    from computer_use.interaction.read.presentation.content import DisplayText, render_text
    from computer_use.interaction.read.presentation.document import DisplayDocument

    if isinstance(value, DisplayDocument):
        return value.text
    if isinstance(value, DisplayText):
        return render_text(value)
    return value


def image_output_scope(png):
    """Return the exact coordinate domain suitable for same-call output reuse.

    Equal pixels alone cannot equate two independent windows. Unregistered
    images have no proved domain and may only reuse the exact source object.
    """
    known = _source(png)
    if known is None or known["source"].get("current", lambda: True)() is False:
        return None
    return known["source"].get("coordinateScope")


@dataclass(frozen=True)
class ImagePointHints:
    """Point syntax and target description derived from current coordinate authority."""

    points: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    target: str | None = None

    def keys(self) -> set[str]:
        return set(self.points) if self.target is None else {*self.points, self.target}


@dataclass(frozen=True)
class ImageCaption:
    """Captured presentation facts and the point hints eligible at emission time."""

    size: tuple[int, int]
    facts: dict
    hints: ImagePointHints

    def render(self, eligible: set[str] | None = None) -> str:
        """Optionally narrow the action hints without changing the captured facts."""
        points = tuple(point for point in self.hints.points if eligible is None or point in eligible)
        target = self.hints.target if points and (eligible is None or self.hints.target in eligible) else None
        caption = render_image_caption(*self.size, dict(self.facts, coordinateTarget=target))
        return "\n".join((caption, IMAGE_POINT_GUIDANCE, *points, *self.hints.actions)) if points else caption


def _point_hints(source, stale=False) -> ImagePointHints:
    if source is None or stale or not source.get("mappingAvailable", True):
        return ImagePointHints()
    if source["kind"] == "desktop":
        return ImagePointHints(("Image point: p = computer.point((x, y))",), ("Global click: computer.click(target=p)",))
    if source["kind"] != "window":
        return ImagePointHints()
    coordinate_windows = source.get("coordinate_windows")
    refs = coordinate_windows() if callable(coordinate_windows) else ()
    if callable(coordinate_windows) and not refs:
        return ImagePointHints()
    application = f"computer.app({source['selector']!r})"
    points = (
        tuple(f"Image point for window={ref}: p = {application}.point((x, y), window={ref})" for ref in refs)
        if refs
        else (f"Image point: p = {application}.point((x, y))",)
    )
    target = source.get("coordinateTarget")
    return ImagePointHints(
        points, (f"App click: {application}.click(at=p)", "Global click (explicit): computer.click(target=p)"),
        target() if callable(target) else target,
    )


def image_point_keys(png) -> set[str]:
    """Return the point hints this capture can still supply after actual delivery."""
    known = _source(png)
    if known is None:
        return set()
    source = known["source"]
    return _point_hints(source, source.get("current", lambda: True)() is False).keys()


def image_caption(png, context_already_shown=False, coordinates: ImageCoordinates | None = None) -> ImageCaption:
    size = png_size(png)
    if coordinates is not None:
        size = {"width": coordinates.delivered_size[0], "height": coordinates.delivered_size[1]}
    known = _source(png)
    source = known["source"] if known is not None else None
    if source is None:
        facts = dict(kind="unknown")
    elif source["kind"] == "desktop":
        facts = dict(kind="desktop", stale=source.get("current", lambda: True)() is False)
    elif source["kind"] == "overview":
        facts = dict(kind="overview", context=_context_text(source.get("context")))
    else:
        context = _context_text(source.get("context"))
        if context is None:
            context = "App selector: " + json_string(source["selector"])
        stale = source.get("current", lambda: True)() is False
        windows = source.get("windows", ())
        identity = (
            ("Window reference: " if len(windows) == 1 else "Window references: ") + ", ".join(map(str, windows))
            if windows
            else "Window reference: unknown"
        )
        if source.get("observed"):
            identity += "; observation=" + json_string(source["observed"]["id"])
        facts = dict(
            kind="window",
            mappingAvailable=source.get("mappingAvailable", True),
            context=context,
            identity=identity,
            stale=stale,
            omitContext=context_already_shown and not source.get("historical", False),
        )
    if source is not None and known["metadata"] is not None:
        facts["stopReasons"] = known["metadata"].get("stopReasons", ())
        facts["capture"] = known["metadata"].get("id")
        # The observation body already carries these sampled window facts.
        if not context_already_shown:
            if "windowStateNotes" in source:
                facts["captureState"] = source["windowStateNotes"]
            else:
                facts["stopReasons"] += window_state_notes(known["metadata"])
        facts["visualText"] = known["metadata"].get("visualText")
        if coordinates is not None and facts["visualText"] is not None:
            visual = facts["visualText"]
            facts["visualText"] = dict(
                visual,
                lines=[dict(line, rect=coordinates.delivered_rect(line["rect"])) for line in visual["lines"]],
            )
    return ImageCaption((size["width"], size["height"]), facts, _point_hints(source, facts.get("stale", False)))


def image_label(png, context_already_shown=False, coordinates: ImageCoordinates | None = None):
    return image_caption(png, context_already_shown, coordinates).render()
