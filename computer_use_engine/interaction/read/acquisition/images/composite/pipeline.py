# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Compose only proved window layouts; retain independent native capture maps."

from __future__ import annotations

import base64
import hashlib
import math
import weakref
from dataclasses import asdict, replace
from io import BytesIO
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.read.acquisition.components.support import ObservationGap
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
from computer_use.session.operations import OperationDeadlineExceeded, current_operation_budget
from PIL import Image

if TYPE_CHECKING:
    from typing import Any, Callable, Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.views.pipeline import WindowPart
    from computer_use.interaction.records import UIHandle
    from computer_use.native.contracts import Binding
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.session.image_coordinates import ImageCoordinates


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

    def run(
        self, ui: UIHandle, parts: Sequence[WindowPart], *, on_gap: Callable[[ObservationGap], None] | None = None,
    ) -> tuple[InterfaceImage | None, list[DisplayText]]:
        """Compose the requested interface image from confirmed member-window captures.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            parts: Ordered source parts whose provenance remains available after composition.
            on_gap: Receive incomplete-composition evidence without discarding already captured pixels.
        """
        from computer_use.interaction.query.instances.scope import allowed

        members = allowed(ui)
        requested = set()
        images = []
        for part in parts:
            key = next(key for key, item in members.items() if item["surface"] == part.target.surface)
            requested.add(key)
            if part.image is not None:
                images.append((key, part.image))
        if not images:
            return None, [
                text(
                    'Screenshot: unavailable; use member-window reads or '
                    'Computer.read(view="image") for visual observation.'
                ),
                DisplayText("image_header", (ImageHeader("unavailable", (), False, (), None),)),
            ]
        unique = dict(images)
        images = list(unique.items())
        collected = tuple(images)
        budget = current_operation_budget()

        def checkpoint() -> None:
            if budget is not None:
                budget.checkpoint()

        try:
            if len(images) > 1 or len(images) != len(requested):
                checkpoint()
            if len(images) != len(requested):
                raise ValueError("Some windows selected for this observation could not be captured.")
            if len({image.coordinates["nativeSpace"] for _, image in images}) != 1:
                raise ValueError("The window captures use different native coordinate spaces.")
            frames = {key: frame(image) for key, image in images} if len(images) > 1 else {}
            for index, (key, image) in enumerate(images):
                for other, _ in images[slice(index + 1, None)]:
                    if overlaps(frames[key], frames[other]) and not (
                        # The pipeline shares retained bindings with their owning service.
                        # pylint: disable-next=protected-access
                        ancestor(cast("DirectoryFacts", ui._instance.directory), key, other)
                        # pylint: disable-next=protected-access
                        or ancestor(cast("DirectoryFacts", ui._instance.directory), other, key)
                    ):
                        raise ValueError(
                            "Overlapping sibling windows have no confirmed stacking order in this driver protocol."
                        )

            def depth(key: str) -> int:
                # pylint: disable-next=protected-access
                return sum(ancestor(ui._instance.directory, key, other) for other in members if key != other)

            images.sort(key=lambda item: depth(item[0]))
            if len(images) == 1:
                key, image = images[0]
                canvas = self.single(key, image, members)
            else:
                left, top = min(v[0] for v in frames.values()), min(v[1] for v in frames.values())
                width = max(v[0] + v[2] for v in frames.values()) - left
                height = max(v[1] + v[3] for v in frames.values()) - top
                scale = min(4096 / max(width, height), max(1 / image.coordinates["toNative"][0] for _, image in images))
                size = math.ceil(width * scale), math.ceil(height * scale)
                checkpoint()
                with Image.new("RGBA", size, (0, 0, 0, 0)) as pixels:
                    pieces = []
                    for key, image in images:
                        checkpoint()
                        x, y, w, h = frames[key]
                        # Integer image geometry is also the exact inverse used
                        # for routing: no nominal-size / rounded-size mismatch.
                        ix, iy = round((x - left) * scale), round((y - top) * scale)
                        iw, ih = max(1, round(w * scale)), max(1, round(h * scale))
                        with Image.open(BytesIO(image.image)) as original:
                            with original.convert("RGBA").resize((iw, ih), Image.Resampling.LANCZOS) as fitted:
                                checkpoint()
                                pixels.alpha_composite(fitted, (ix, iy))
                        pieces.append(ImagePiece(key, image, (ix, iy, iw, ih)))
                    checkpoint()
                    buffer = BytesIO()
                    pixels.save(buffer, format="PNG")
                    checkpoint()
                stamps = [capture_metadata(image.image) for _, image in images]
                # pylint: disable-next=protected-access
                scope = "ui:" + images[0][1].target.surface + ":" + str(ui._anchor)
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
        except OperationDeadlineExceeded as error:
            # A full composition is optional CPU work. Return one exact native
            # image that was already captured, with its own displayed pixels
            # and original native map; never relabel an unseen crop/composite.
            selected = collected[0]
            fixed = getattr(ui, "_window_identity", None)
            if fixed is not None:
                selected = next((
                    (key, image) for key, image in collected
                    if key == fixed.root_window
                    and (image.target.session, image.target.root_window, image.target.surface, image.target.instance)
                    == (fixed.session, fixed.root_window, fixed.surface, fixed.instance)
                ), selected)
            key, image = selected
            canvas = self.single(key, image, members)
            displayed, kind = (key,), "single"
            ref = next(ref for ref, window in ui._instance.windows.items() if window == key)
            notes = [template(
                "Screenshot: original Window {} image; coordinates use this image's pixels. "
                "Other captured windows were omitted when the composition budget ended.", ref,
            )]
            if on_gap is not None:
                on_gap(ObservationGap(
                    "image", "observation_incomplete",
                    f"Image composition budget exhausted; only Window {ref}'s original image was included. {error}",
                    f"Use this displayed image with computer.window({ref}); inspect other windows if needed. "
                    "Do not replay input.",
                ))
        except (ValueError, OSError) as error:
            key, image = images[0]
            canvas = InterfaceImage(image.image, (), signature(members), unavailable_mapping=True)
            # pylint: disable-next=protected-access
            ref = next(ref for ref, window in ui._instance.windows.items() if window == key)
            displayed, kind = (key,), "partial"
            notes = [
                template(
                    "Screenshot: partial; shows only window {}, not the complete instance layout. {}", ref, str(error)
                ),
                template(
                    (
                        "Coordinates unavailable on this partial canvas; use "
                        'app.window({}).read(view="image") or '
                        'Computer.read(view="image").'
                    ),
                    ref,
                ),
            ]
        summary = self.metadata(ui, canvas, collected, displayed, kind)
        target = canvas.pieces[0].image.target if canvas.pieces else images[0][1].target
        self.register(ui, canvas, target, summary.windows)
        notes.append(DisplayText("image_header", (summary,)))
        return canvas, notes

    @staticmethod
    def single(key, image, members):
        """Retain an original PNG as its explicitly displayed single-window canvas."""
        metadata = capture_metadata(image.image)
        return InterfaceImage(
            image.image,
            (ImagePiece(key, image, (0, 0, metadata["width"], metadata["height"])),),
            signature(members),
        )

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

    def register(self, ui: UIHandle, canvas: InterfaceImage, target: Binding, windows: tuple[int, ...]) -> None:
        """Retain the composite image's source windows and coordinate mapping.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            target: A captured source identity whose native session owns this image.
            windows: Public references for the windows actually displayed in this PNG.
        """
        adapters = self.controller.adapters
        canvas.digest = hashlib.sha256(canvas.png).digest()
        # pylint: disable-next=protected-access
        state = ui._instance
        fixed_window = ui._anchor is not None
        previous = (
            self.controller.interfaces.windows.delivered_image(state, ui) if fixed_window else state.delivered_image
        )
        state.image_generation += 1
        generation = state.image_generation
        canvas.generation = generation
        from computer_use.session.interfaces import WindowIdentity

        for piece in canvas.pieces:
            identity = WindowIdentity(piece.window, piece.image.target.surface, piece.image.target.instance)
            state.sampled_window_images[identity] = canvas
        self.controller.transport.capture_references = self.controller.interfaces.windows.capture_references()

        def sources(value: Any) -> tuple[Any, ...]:
            return tuple((piece.window, replace(piece.image.target, capture=None), piece.box) for piece in value.pieces)

        matches_previous = (
            True
            if (
                previous is not None
                and not previous.expired
                and not previous.unavailable_mapping
                and previous.delivered == adapters.image_delivery_epoch()
                and previous.digest == canvas.digest
                and previous.membership == canvas.membership
                and sources(previous) == sources(canvas)
            )
            else False
        )
        if matches_previous:
            # Knowledge of identical pixels can survive a fresh capture. The
            # command still dispatches through its new native token and map.
            canvas.delivered = previous.delivered
            canvas.delivered_coordinates = previous.delivered_coordinates
        value, application = weakref.ref(canvas), weakref.ref(state)
        # pylint: disable-next=protected-access
        session = target.session
        controller = weakref.ref(self.controller)

        def valid() -> bool:
            image, native, state = value(), controller(), application()
            if (
                image is None
                or native is None
                or state is None
                or image.expired
                or native.transport.session != session
                or hashlib.sha256(image.png).digest() != image.digest
            ):
                return False
            return True

        def coordinate_windows() -> tuple[int, ...]:
            if not valid():
                return ()
            image, state = value(), application()
            identities = {
                piece.window: (piece.image.target.surface, piece.image.target.instance) for piece in image.pieces
            }
            available = []
            from computer_use.session.interfaces import WindowIdentity, WindowLifetime

            for ref, window in state.windows.items():
                if window not in identities:
                    continue
                surface, instance = identities[window]
                identity = WindowIdentity(window, surface, instance)
                evidence = state.evidence.get(identity)
                member = state.members.get(window)
                retained = evidence is not None and evidence.lifetime is not WindowLifetime.RETIRED
                captured_member = member is not None and WindowIdentity.of(member) == identity
                if (retained or (evidence is None and captured_member)) and (
                    generation >= state.window_image_generations.get(identity, -1)
                ):
                    available.append(ref)
            return tuple(available)

        def current() -> bool:
            image = value()
            return valid() and (image.unavailable_mapping or bool(coordinate_windows()))

        def delivered(coordinates: ImageCoordinates | None = None) -> None:
            if current():
                image, state, native = value(), application(), controller()
                image.delivered = adapters.image_delivery_epoch()
                image.delivered_coordinates = coordinates
                if generation > state.delivered_image_generation:
                    state.delivered_image = image
                    state.delivered_image_generation = generation
                native.interfaces.windows.retain_image(state, image, generation=generation)
                native.transport.capture_references = native.interfaces.windows.capture_references()

        adapters.register_image(
            canvas.png,
            dict(
                kind="window",
                windows=windows,
                mappingAvailable=not canvas.unavailable_mapping,
                # pylint: disable-next=protected-access
                selector=str(ui._instance.selector),
                context=template(
                    "App {}; {}",
                    # pylint: disable-next=protected-access
                    ui._instance.name or str(ui._instance.selector),
                    # pylint: disable-next=protected-access
                    "observed windows" if ui._anchor is None else "fixed Window",
                ),
                current=current,
                delivered=delivered,
                coordinate_windows=coordinate_windows,
                coordinateScope=(
                    "window", session,
                    tuple(
                        (piece.window, piece.image.target.instance, piece.image.target.surface, piece.box,
                         capture_metadata(piece.image.image)["width"], capture_metadata(piece.image.image)["height"])
                        for piece in canvas.pieces
                    ),
                ) if canvas.pieces else None,
            ),
        )
