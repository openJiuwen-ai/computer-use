"""Image presentation backed by the session's single provenance store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.image_caption import png_summary, render_image_caption, window_state_notes
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


def image_label(png, context_already_shown=False):
    size = png_size(png)
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
        identity = "Window reference: " + (source.get("window") if source.get("window") is not None else "unknown")
        if source.get("observed"):
            identity += "; observation=" + json_string(source["observed"]["id"])
        facts = dict(
            kind="window",
            context=context,
            identity=identity,
            stale=stale,
            omitContext=context_already_shown and source.get("current", lambda: True)() is not False,
        )
    if source is not None and known["metadata"] is not None:
        facts["stopReasons"] = known["metadata"].get("stopReasons", ())
        # App.read already shows short state facts. If that context was shown,
        # retain only the detailed repaint guidance in the image caption.
        notes = window_state_notes(known["metadata"])
        facts["stopReasons"] += notes[1:] if facts.get("omitContext") else notes
        facts["visualText"] = known["metadata"].get("visualText")
    return render_image_caption(size["width"], size["height"], facts)
