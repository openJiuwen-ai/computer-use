# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compose only proved window layouts; retain independent native capture maps."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from typing import Any, Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.views.pipeline import WindowPart
    from computer_use.interaction.records import UIHandle
import base64
import hashlib
import math
import weakref
from dataclasses import asdict, replace
from io import BytesIO
from uuid import uuid4

from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.read.presentation.content import DisplayText, template, text
from computer_use.interaction.read.presentation.headers import ImageHeader
from computer_use.protocol.captures import capture_metadata, decode_capture
from computer_use.session.interface_images import (
    ImagePiece,
    InterfaceImage,
    frame,
    overlaps,
    signature,
)
from PIL import Image


class InterfaceImagePipeline:
    """Compose member-window captures into an interface image.

    Arrange confirmed captures using native geometry and known window relationships,
    retaining source-to-canvas mappings. Return the composite and capture notes, or a
    clearly partial image when a complete mapped canvas cannot be built.

    Attributes:
        controller: Session services used to register capture identities and coordinate
            mappings.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller

    def run(self, ui: UIHandle, parts: Sequence[WindowPart]) -> tuple[InterfaceImage | None, list[DisplayText]]:
        """Compose the requested interface image from confirmed member-window captures.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            parts: Ordered source parts whose provenance remains available after composition.
        """
        from computer_use.interaction.query.instances.scope import allowed

        members = allowed(ui)
        images = [
            (next(key for key, item in members.items() if item["surface"] == part.target.surface), part.image)
            for part in parts
            if part.image is not None
        ]
        if not images:
            return None, [
                text(
                    'Screenshot: unavailable; use member-window reads or Computer.read(view="image") for visual observation.'
                ),
                DisplayText("image_header", (ImageHeader("unavailable", (), False, (), None),)),
            ]
        unique = dict(images)
        images = list(unique.items())
        collected = tuple(images)
        try:
            if len(images) != len(members):
                raise ValueError("Some member windows could not be captured or exceeded the read budget.")
            if len({image.coordinates["nativeSpace"] for _, image in images}) != 1:
                raise ValueError("The window captures use different native coordinate spaces.")
            frames = {key: frame(image) for key, image in images} if len(images) > 1 else {}
            for index, (key, image) in enumerate(images):
                for other, _ in images[index + 1 :]:
                    if overlaps(frames[key], frames[other]) and not (
                        ancestor(cast("DirectoryFacts", ui._instance.directory), key, other)
                        or ancestor(cast("DirectoryFacts", ui._instance.directory), other, key)
                    ):
                        raise ValueError(
                            "Overlapping sibling windows have no confirmed stacking order in this driver protocol."
                        )

            def depth(key: str) -> int:
                return sum(ancestor(ui._instance.directory, key, other) for other in members if key != other)

            images.sort(key=lambda item: depth(item[0]))
            if len(images) == 1:
                key, image = images[0]
                metadata = capture_metadata(image.image)
                canvas = InterfaceImage(
                    image.image,
                    (ImagePiece(key, image, (0, 0, metadata["width"], metadata["height"])),),
                    signature(members),
                )
            else:
                left, top = min(v[0] for v in frames.values()), min(v[1] for v in frames.values())
                width = max(v[0] + v[2] for v in frames.values()) - left
                height = max(v[1] + v[3] for v in frames.values()) - top
                scale = min(4096 / max(width, height), max(1 / image.coordinates["toNative"][0] for _, image in images))
                size = math.ceil(width * scale), math.ceil(height * scale)
                with Image.new("RGBA", size, (0, 0, 0, 0)) as pixels:
                    pieces = []
                    for key, image in images:
                        x, y, w, h = frames[key]
                        # Integer image geometry is also the exact inverse used
                        # for routing: no nominal-size / rounded-size mismatch.
                        ix, iy = round((x - left) * scale), round((y - top) * scale)
                        iw, ih = max(1, round(w * scale)), max(1, round(h * scale))
                        with Image.open(BytesIO(image.image)) as original:
                            with original.convert("RGBA").resize((iw, ih), Image.Resampling.LANCZOS) as fitted:
                                pixels.alpha_composite(fitted, (ix, iy))
                        pieces.append(ImagePiece(key, image, (ix, iy, iw, ih)))
                    buffer = BytesIO()
                    pixels.save(buffer, format="PNG")
                stamps = [capture_metadata(image.image) for _, image in images]
                scope = "ui:" + cast("WorkingWindow", ui._instance.anchor).root.surface + ":" + str(ui._anchor)
                png = decode_capture(
                    dict(
                        id="canvas:" + uuid4().hex,
                        kind="window",
                        scope=scope,
                        startedAt=min(stamp["startedAt"] for stamp in stamps),
                        completedAt=max(stamp["completedAt"] for stamp in stamps),
                        width=size[0],
                        height=size[1],
                        png=base64.b64encode(buffer.getvalue()).decode("ascii"),
                    ),
                    "window",
                    scope,
                )
                canvas = InterfaceImage(png, tuple(pieces), signature(members), (left, top), scale)
            displayed = tuple(key for key, _ in images)
            kind = "single" if len(images) == 1 else "composite"
            notes = []
        except (ValueError, OSError) as error:
            key, image = images[0]
            canvas = InterfaceImage(image.image, (), signature(members), unavailable_mapping=True)
            ref = next(ref for ref, window in ui._instance.windows.items() if window == key)
            displayed, kind = (key,), "partial"
            notes = [
                template(
                    "Screenshot: partial; shows only window {}, not the complete instance layout. {}", ref, str(error)
                ),
                template(
                    'Coordinates unavailable on this partial canvas; use app.window({}).read(view="image") or Computer.read(view="image").',
                    ref,
                ),
            ]
        self.register(ui, canvas)
        summary = self.metadata(ui, canvas, collected, displayed, kind)
        notes.append(DisplayText("image_header", (summary,)))
        return canvas, notes

    @staticmethod
    def metadata(
        ui: UIHandle,
        canvas: InterfaceImage,
        images: Sequence[tuple[str, WindowImage]],
        displayed: tuple[str, ...],
        kind: Literal["single", "composite", "partial"],
    ) -> ImageHeader:
        """Freeze original captures and the actual displayed sources before publication.

        Partial canvases retain their selected source independently of empty pieces.
        Pixel composition, image registration and input mappings stay unchanged.
        """
        aliases = {window: ref for ref, window in ui._instance.windows.items()}
        captures = tuple(
            dict(
                window=key,
                ref=aliases[key],
                displayed=key in displayed,
                target=asdict(image.target),
                observed=image.observed,
                coordinates=image.coordinates,
                capture=capture_metadata(image.image),
            )
            for key, image in images
        )
        return ImageHeader(
            kind,
            tuple(aliases[key] for key in displayed),
            not canvas.unavailable_mapping,
            captures,
            capture_metadata(canvas.png),
            tuple((piece.window, piece.box) for piece in canvas.pieces),
            canvas.origin,
            canvas.scale,
            canvas.membership,
        )

    def register(self, ui: UIHandle, canvas: InterfaceImage) -> None:
        """Retain the composite image's source windows and coordinate mapping.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
        """
        adapters = self.controller.adapters
        canvas.digest = hashlib.sha256(canvas.png).digest()
        previous = ui._image

        def sources(value: Any) -> tuple[Any, ...]:
            return tuple((piece.window, replace(piece.image.target, capture=None), piece.box) for piece in value.pieces)

        if (
            previous is not None
            and not previous.expired
            and not previous.unavailable_mapping
            and previous.delivered == adapters.image_delivery_epoch()
            and previous.digest == canvas.digest
            and previous.membership == canvas.membership
            and sources(previous) == sources(canvas)
        ):
            # Knowledge of identical pixels can survive a fresh capture. The
            # command still dispatches through its new native token and map.
            canvas.delivered = previous.delivered
        owner, value = weakref.ref(ui), weakref.ref(canvas)
        session = cast("WorkingWindow", ui._instance.anchor).root.session
        controller = weakref.ref(self.controller)

        def current() -> bool:
            target, image, native = owner(), value(), controller()
            return (
                target is not None
                and image is not None
                and native is not None
                and target._image is image
                and not image.expired
                and native.transport.session == session
                and hashlib.sha256(image.png).digest() == image.digest
            )

        def delivered() -> None:
            if current():
                value().delivered = adapters.image_delivery_epoch()

        adapters.register_image(
            canvas.png,
            dict(
                kind="window",
                selector=str(ui._instance.selector),
                context=template(
                    "App instance {}; {}",
                    cast("WorkingWindow", ui._instance.anchor).name,
                    "overall view" if ui._anchor is None else "fixed Window",
                ),
                current=current,
                delivered=delivered,
            ),
        )


if TYPE_CHECKING:
    from computer_use.native.scopes import WorkingWindow
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
