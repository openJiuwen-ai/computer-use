"""Python window thumbnails from independent native captures, with no input map.

The grid is a contact sheet, never a reconstruction of native z-order or window
parentage. Each tile retains its own observed identity and capture interval.
"""

from __future__ import annotations

import base64
import math
import uuid
from dataclasses import dataclass, replace
from functools import cached_property
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageOps

from computer_use.interaction.read.acquisition.components.support import collect_component
from computer_use.interaction.read.acquisition.directory.service import select_surface
from computer_use.interaction.read.acquisition.images.capture import NativeImages, WindowImage
from computer_use.interaction.read.presentation.content import DisplayText, join, json_value, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.protocol.captures import captureMetadata, decodeCapture
from computer_use.protocol.png import PNG
from computer_use.protocol.primitives import frozen

if TYPE_CHECKING:
    from typing import Any, Mapping, Sequence

    from computer_use.interaction.records import Remaining
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts, Record


@dataclass(frozen=True)
class WindowPreview:
    """Window identity and its captured preview or collection error.

    Attributes:
        reference: Display alias assigned to this window preview.
        window: Native surface identity of the previewed window.
        title: Observed title used to label the preview.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        error: Capture failure text retained instead of a preview image.
    """

    reference: str
    window: str
    title: str
    image: WindowImage | None
    error: str | None = None


@dataclass(frozen=True)
class WindowOverview:
    """Composite window preview and the source metadata needed to interpret it.

    Attributes:
        image: Captured pixels and their original coordinate/identity metadata, when available.
        document: Structured preview labels and captured image limitations awaiting output.
        sources: Capture geometry and identities for the windows in the composite image.
    """

    image: PNG | None
    document: DisplayDocument
    sources: tuple[Record, ...]

    @cached_property
    def text(self) -> str:
        """Render this captured overview on first explicit text access."""
        return self.document.text


def _state_notes(metadata: Mapping[str, Any]) -> tuple[DisplayText, ...]:
    """Retain independent capture-state samples as deferred diagnostic fields."""
    state = metadata.get("windowState")
    if state is None:
        return ()

    def label(value: bool | None) -> str:
        return "unknown" if value is None else ("true" if value else "false")

    notes = [
        template(
            "Window state (independent samples during capture): "
            "onScreen={}; ownerForeground={}. These independent samples do not measure occlusion or repaint.",
            label(state["onScreen"]),
            label(state["ownerForeground"]),
        )
    ]
    if state["onScreen"] is False or state["ownerForeground"] is False:
        notes.append(template("Off-screen window or background owner; pixels may lag accessibility."))
    return tuple(notes)


def compose_overview(
    previews: Sequence[WindowPreview],
    omitted: Sequence[str],
    *,
    scope: str,
    max_dimension: int = 1600,
) -> WindowOverview:
    """A bounded, uncropped preview; independent window images remain unchanged.

    Args:
        scope: Current traversal or execution scope; independent windows remain boundaries.
        max_dimension: Maximum image edge length in pixels, preserving aspect ratio.
    """
    if not 1 <= len(previews) <= 6 or type(max_dimension) is not int or not 320 <= max_dimension <= 1600:
        raise ValueError("Invalid overview layout budget")
    columns = min(3, len(previews))
    tile_width = max_dimension // columns
    tile_height = min(340, tile_width * 3 // 4)
    caption, padding = 28, 8
    rows = math.ceil(len(previews) / columns)
    canvas = Image.new("RGB", (tile_width * columns, tile_height * rows), "#171b22")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=16)
    sources, lines, intervals = [], [], []
    for index, preview in enumerate(previews):
        x, y = (index % columns) * tile_width, (index // columns) * tile_height
        draw.text((x + padding, y + 5), "Window " + preview.reference, font=font, fill="#e5eaf0")
        draw.rectangle((x + padding, y + caption, x + tile_width - padding, y + tile_height - padding), fill="#252c36")
        source: Record = dict(
            reference=preview.reference,
            window=preview.window,
            title=preview.title,
            capture=None,
            observed=None,
            error=preview.error,
        )
        if preview.image is not None:
            metadata = captureMetadata(preview.image.image)
            if (
                metadata is None
                or metadata["kind"] != "window"
                or metadata["id"] != preview.image.target.capture
                or metadata["scope"] != preview.image.target.surface
            ):
                raise ValueError("Overview input is not an independently observed window capture")
            # The native protocol already validated PNG dimensions and framing.
            # Decode one image at a time and retain only source metadata below.
            source.update(capture=metadata["id"], observed=preview.image.observed)
            if "windowState" in metadata:
                source["windowState"] = metadata["windowState"]
            if metadata.get("stopReasons"):
                source["stopReasons"] = metadata["stopReasons"]
            try:
                with Image.open(BytesIO(preview.image.image), formats=["PNG"]) as pixels:
                    with pixels.convert("RGBA") as rgba:
                        with ImageOps.contain(
                            rgba, (tile_width - 2 * padding, tile_height - caption - padding)
                        ) as fitted:
                            position = (
                                x + (tile_width - fitted.width) // 2,
                                y + caption + (tile_height - caption - padding - fitted.height) // 2,
                            )
                            canvas.paste(fitted, position, fitted)
                intervals.append((metadata["startedAt"], metadata["completedAt"]))
            except OSError as error:
                source["error"] = "PNG thumbnail decoding failed: " + str(error)
        if preview.image is None or source["error"]:
            draw.text((x + 2 * padding, y + caption + padding), "Preview unavailable", font=font, fill="#e5eaf0")
        sources.append(frozen(source))
        line = template("Window {}: {}", preview.reference, json_value(preview.title))
        if source["error"]:
            line = template("{}; preview unavailable: {}", line, source["error"])
        lines.append(line)
        lines.extend(template("  Image note: {}", json_value(reason)) for reason in source.get("stopReasons", ()))
        lines.extend(template("  Image note: {}", json_value(reason)) for reason in _state_notes(source))
    header = (
        "Overview: window thumbnails; context only, not desktop layout or click coordinates. "
        "Use app.window(ref).read() for a window image. Captures are separate observations."
    )
    if omitted:
        lines.append(
            template(
                "Not pictured (overview budget): {}; select a window explicitly to read it.",
                join(omitted, separator=", "),
            )
        )
    image = None
    if intervals:
        buffer = BytesIO()
        canvas.save(buffer, format="PNG")
        limited = [source["reference"] for source in sources if source.get("stopReasons")]
        # The full diagnostics remain on each source and in its text. A bounded
        # summary also travels with the composed PNG, including standalone display.
        notes = (
            dict(
                stopReasons=[
                    "Window previews have capture limitations: " + ", ".join(limited) + ". See per-window image notes."
                ]
            )
            if limited
            else {}
        )
        image = decodeCapture(
            dict(
                id="overview:" + uuid.uuid4().hex,
                kind="overview",
                scope=scope,
                **notes,
                startedAt=min(i[0] for i in intervals),
                completedAt=max(i[1] for i in intervals),
                width=canvas.width,
                height=canvas.height,
                png=base64.b64encode(buffer.getvalue()).decode("ascii"),
            ),
            "overview",
            scope,
        )
    canvas.close()
    return WindowOverview(image, DisplayDocument.join((header, *lines)), tuple(sources))


class NativeOverview:
    """Capture and arrange previews of related or selectable application windows."""

    def __init__(self, port: NativePort) -> None:
        self.port = port
        self.images = NativeImages(port)

    async def collect(
        self,
        directory: DirectoryFacts,
        references: Mapping[str, str],
        remaining: Remaining,
        *,
        current: WindowImage | None = None,
        root: str | None = None,
        include_single: bool = False,
    ) -> WindowOverview | None:
        """Capture candidate windows and compose their labelled overview previews.

        Args:
            directory: Observed application/window catalogue, including incomplete
                relationships.
            references: Existing session reference allocator; never a new per-pass identity
                cache.
            remaining: Callback returning milliseconds left on the original call; may raise on
                expiration.
            current: Currently confirmed target or catalogue; absence is not a guessed fallback.
            root: Root of the requested semantic view; native bindings remain separate.
            include_single: Whether to create an overview even when only one window was observed.
        """
        if not directory["surfaces"] or len(directory["surfaces"]) == 1 and not include_single:
            return None
        if current is not None and current.target.session != directory["session"]:
            raise ValueError("A previous session image cannot be reused in an overview")
        aliases = {window: ref for ref, window in references.items()}
        entries = list(directory["surfaces"])
        # Prefer the current image and its working root, then modal/unknown
        # windows. Membership still comes exclusively from the native directory.
        entries.sort(
            key=lambda item: (
                0
                if current is not None and item["surface"] == current.target.surface
                else 1
                if item["window"] == root
                else 2
                if item["modal"] is True
                else 3
                if item["parentState"] == "unknown"
                else 4
            )
        )
        previews = []
        pictured = entries[:6]
        for index, entry in enumerate(pictured):
            image, error = None, None
            if current is not None and entry["surface"] == current.target.surface:
                image = current
            elif "nativeCapture" not in self.port.capabilities:
                error = "window capture is not supported by this driver"
            else:

                async def capture(*, entry: Record = entry, index: int = index) -> WindowImage:
                    target = select_surface(directory, entry["window"], follow_modal=False)
                    async with self.port.calls.operation(replace(target, node=None, capture=None), remaining) as read:
                        return await self.images.collect(
                            read.bounded(max(1, min(3000, remaining() // (2 * (len(pictured) - index))))),
                            max_dimension=720,
                        )

                image, gap = await collect_component(self.port, "overview", capture)
                if gap:
                    error = gap.detail
            previews.append(WindowPreview(aliases[entry["window"]], entry["window"], entry["title"], image, error))
        remaining()
        overview = compose_overview(
            previews, [aliases[item["window"]] for item in entries[6:]], scope="catalogue:" + directory["application"]
        )
        remaining()
        return overview
