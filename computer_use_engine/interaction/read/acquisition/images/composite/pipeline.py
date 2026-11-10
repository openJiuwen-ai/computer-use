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
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.presentation.content import DisplayText, join, template, text
from computer_use.interaction.read.presentation.headers import ImageHeader, image_state_notes
from computer_use.protocol.captures import capture_metadata, decode_capture
from computer_use.session.interface_images import (
    ImagePiece,
    InterfaceImage,
    content_signature,
    frame,
    overlaps,
    shared_pixel_signature,
    signature,
)
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from computer_use.session.operations import OperationDeadlineExceeded, current_operation_budget
from computer_use.session.surface_content import display_host
from PIL import Image

if TYPE_CHECKING:
    from typing import Any, Callable, Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.views.pipeline import WindowPart
    from computer_use.interaction.records import UIHandle
    from computer_use.native.contracts import Binding
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
            ui: App or request scope whose instance and observation bounds constrain the
                operation.
            parts: Ordered source parts whose provenance remains available after composition.
            on_gap: Receive incomplete-composition evidence without discarding already captured pixels.
        """
        from computer_use.interaction.query.instances.scope import allowed

        members = dict(allowed(ui))
        # UIHandle exposes the separate instance catalogue to internal pipelines.
        # pylint: disable-next=protected-access
        source_directories = [ui._instance.directory]
        confirmed, aliases = {}, {}
        requested, parent_bound = set(), set()
        images = []
        for part in parts:
            identity = part.identity
            key = identity.window
            if key is None:
                continue
            current = members.get(key)
            current_matches = current is not None and (
                current["window"], current["surface"], current["instance"]
                # UIHandle exposes the separate instance catalogue to internal pipelines.
                # pylint: disable-next=protected-access
            ) == (identity.window, identity.surface, identity.instance) and part.target.session == ui._instance.session
            source = part.source
            # A parent-anchored capture supplies the member's pixels. The
            # existing coordinate contract requires the member's own binding.
            current_capture = current_matches and (source is None or source.scope_confirmed)
            image = part.image
            if current_matches and image is not None and image.rechecked_scope is not None:
                # A later tree collection may replace the source observation
                # without replacing this capture or its exact member proof.
                current_capture = (
                    image.target.root_window == key
                    # UIHandle exposes the separate instance catalogue to internal pipelines.
                    # pylint: disable-next=protected-access
                    and image.rechecked_scope == image.member_scope(ui._instance.directory)
                )
            if current_capture and part.target.root_window != key:
                parent_bound.add(identity)
            confirmed[identity] = current_capture and identity not in parent_bound
            if not confirmed[identity] and source is not None:
                directory = source.source_directory
                if directory is not None and (
                    directory["session"], directory["application"]
                    # UIHandle exposes the separate instance catalogue to internal pipelines.
                    # pylint: disable-next=protected-access
                ) == (part.target.session, ui._instance.selector):
                    original = next(
                        (item for item in directory["surfaces"] if WindowIdentity.of(item) == identity), None,
                    )
                    if original is not None:
                        # This is presentation evidence only. It never restores
                        # omitted/ended objects to the current instance catalogue.
                        members.setdefault(key, original)
                        source_directories.append(directory)
            aliases[identity] = (
                next(
                    # UIHandle exposes the separate instance catalogue to internal pipelines.
                    # pylint: disable-next=protected-access
                    (ref for ref, window in ui._instance.windows.items() if window == display_host(members, key)), None,
                )
                if confirmed[identity] else self.controller.interfaces.windows.reference(
                    # UIHandle exposes the separate instance catalogue to internal pipelines.
                    # pylint: disable-next=protected-access
                    part.target.session, ui._instance.selector, identity
                )
            )
            requested.add(identity)
            if part.image is not None:
                images.append((identity, part.image))
        if not images:
            return None, [
                text("Screenshot: unavailable."),
                DisplayText("image_header", (ImageHeader("unavailable", (), False, (), None),)),
            ]
        unique = dict(images)
        images = list(unique.items())
        collected = tuple(images)
        budget = current_operation_budget()
        layout_notes = ()
        missing = requested - unique.keys()
        shared = dict(shared_pixel_signature(members))
        captured_windows = {identity.window for identity in unique if confirmed[identity]}
        missing = {identity for identity in missing if shared.get(identity.window) not in captured_windows}
        capture_notes = []
        if missing:
            detail = "Image unavailable for: " + ", ".join(sorted(key.window for key in missing)) + "."
            capture_notes.append(text(detail))
            if on_gap is not None:
                on_gap(ObservationGap("image", "observation_incomplete", detail))

        def related(child: WindowIdentity, parent: WindowIdentity) -> bool:
            for directory in source_directories:
                rows = {item["window"]: item for item in directory["surfaces"]}
                if ancestor(directory, child.window, parent.window) and all(
                    key.window in rows and WindowIdentity.of(rows[key.window]) == key for key in (child, parent)
                ):
                    return True
            return False

        def checkpoint() -> None:
            if budget is not None:
                budget.checkpoint()

        try:
            if len(images) > 1 or len(images) != len(requested):
                checkpoint()
            separate_states = len({key.window for key, _ in images}) != len(images)
            separate_views = separate_states or len({image.coordinates["nativeSpace"] for _, image in images}) != 1
            frames = {}
            if len(images) > 1 and not separate_views:
                try:
                    frames = {key: frame(image) for key, image in images}
                except ValueError:
                    # Native layout is optional: each capture still has its own
                    # pixel-to-native transform, including nonrectangular maps.
                    separate_views = True
                if not separate_views:
                    separate_views = any(
                        overlaps(frames[key], frames[other])
                        and not (related(key, other) or related(other, key))
                        for index, (key, _) in enumerate(images) for other, _ in images[index + 1:]
                    )
            if separate_views:
                # Unknown stacking is not missing input authority. Arrange
                # original captures in distinct pixel regions; each region
                # retains its own native recipient and inverse mapping.
                left = 0
                for key, image in images:
                    metadata = capture_metadata(image.image)
                    width, height = metadata["width"], metadata["height"]
                    frames[key] = left, 0, width, height
                    left += width + 16

            def depth(key: WindowIdentity) -> int:
                # pylint: disable-next=protected-access
                return sum(related(key, other) for other, _ in collected if key != other)

            if not separate_views:
                images.sort(key=lambda item: depth(item[0]))
            if len(images) == 1:
                key, image = images[0]
                canvas = self.single(key, image, members, scope_confirmed=confirmed[key])
            else:
                left, top = min(v[0] for v in frames.values()), min(v[1] for v in frames.values())
                width = max(v[0] + v[2] for v in frames.values()) - left
                height = max(v[1] + v[3] for v in frames.values()) - top
                density = 1.0 if separate_views else max(1 / image.coordinates["toNative"][0] for _, image in images)
                scale = min(4096 / max(width, height), density)
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
                        pieces.append(ImagePiece(key.window, image, (ix, iy, iw, ih), confirmed[key]))
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
                canvas = InterfaceImage(
                    png, tuple(pieces), signature(members), (left, top), scale,
                    content_hosts=content_signature(members),
                    shared_pixels=shared_pixel_signature(members),
                    identities=tuple(WindowIdentity.of(members[key]) for key in sorted(members)),
                )
            displayed = tuple(key for key, _ in images)
            kind = "partial" if missing else "single" if len(images) == 1 else "composite"
            notes = list(capture_notes)
            if separate_views:
                layout_notes = (text(
                    "Screenshot: separate captured window states; arrangement is not a live window layout."
                    if separate_states else
                    "Screenshot: separate captured views; panel positions do not represent desktop positions."
                ),)
                notes.extend(layout_notes)
                if separate_states and on_gap is not None:
                    on_gap(ObservationGap(
                        "image", "observation_incomplete",
                        "The displayed canvas includes historical and current window lifetimes "
                        "as separate captured states.",
                    ))
        except OperationDeadlineExceeded as error:
            # A full composition is optional CPU work. Return one exact native
            # image that was already captured, with its own displayed pixels
            # and original native map; never relabel an unseen crop/composite.
            selected = collected[0]
            fixed = getattr(ui, "_window_identity", None)
            if fixed is not None:
                for key, image in collected:
                    if key.window != fixed.root_window:
                        continue
                    captured = image.target
                    if (captured.session, captured.root_window, captured.surface, captured.instance) == (
                        fixed.session, fixed.root_window, fixed.surface, fixed.instance,
                    ):
                        selected = key, image
                        break
            key, image = selected
            canvas = self.single(key, image, members, scope_confirmed=confirmed[key])
            displayed, kind = (key,), "single"
            ref = aliases[key] if aliases[key] is not None else key.window
            notes = [template(
                "Screenshot: original Window {} image; coordinates use this image's pixels. "
                "Other captured windows were omitted when the composition budget ended.", ref,
            )]
            if on_gap is not None:
                on_gap(ObservationGap(
                    "image", "observation_incomplete",
                    f"Image composition budget exhausted; only Window {ref}'s original image was included. {error}",
                ))
        except (ValueError, OSError) as error:
            key, image = collected[0]
            canvas = self.single(key, image, members, scope_confirmed=confirmed[key])
            # pylint: disable-next=protected-access
            ref = aliases[key] if aliases[key] is not None else key.window
            displayed, kind = (key,), "partial"
            notes = [*capture_notes,
                template("Screenshot: partial; window {}. {}", ref, str(error)),
            ]
            if on_gap is not None:
                on_gap(ObservationGap(
                    "image", "observation_incomplete",
                    f"Image composition failed; Window {ref}'s original image is retained. {error}",
                ))
        historical = tuple(template(
            "Window {} image: historical capture ({}–{} native ns); current scope unconfirmed; "
            "coordinate mapping unavailable for this window.",
            aliases[key] if aliases[key] is not None else key.window,
            capture_metadata(image.image)["startedAt"], capture_metadata(image.image)["completedAt"],
        ) for key, image in collected if key in displayed and not confirmed[key] and key not in parent_bound)
        notes.extend(historical)
        notes.extend(template(
            "Window {} image: captured through its parent scope; coordinate mapping unavailable for this window.",
            aliases[key] if aliases[key] is not None else key.window,
        ) for key in displayed if key in parent_bound)
        summary = self.metadata(canvas, collected, displayed, kind, aliases, confirmed)
        target = canvas.pieces[0].image.target if canvas.pieces else images[0][1].target
        self.register(
            ui, canvas, target, summary, historical=(*layout_notes, *historical),
            has_confirmed_pixels=any(confirmed[key] for key in displayed),
        )
        notes.append(DisplayText("image_header", (summary,)))
        return canvas, notes

    @staticmethod
    def single(key, image, members, *, scope_confirmed=True):
        """Retain an original PNG as its explicitly displayed single-window canvas."""
        metadata = capture_metadata(image.image)
        return InterfaceImage(
            image.image,
            (ImagePiece(key.window, image, (0, 0, metadata["width"], metadata["height"]), scope_confirmed),),
            signature(members),
            content_hosts=content_signature(members),
            shared_pixels=shared_pixel_signature(members),
            identities=tuple(WindowIdentity.of(members[key]) for key in sorted(members)),
        )

    @staticmethod
    def metadata(
        canvas: InterfaceImage,
        images: Sequence[tuple[WindowIdentity, WindowImage]],
        displayed: tuple[WindowIdentity, ...],
        kind: Literal["single", "composite", "partial"],
        aliases: dict[WindowIdentity, int | None],
        confirmed: dict[WindowIdentity, bool],
    ) -> ImageHeader:
        """Freeze original captures and the actual displayed sources before publication.

        Partial canvases retain their selected source independently of empty pieces.
        Pixel composition, image registration and input mappings stay unchanged.
        """
        captures = tuple(
            dict(
                window=key.window,
                ref=aliases[key],
                displayed=key in displayed,
                scopeConfirmed=confirmed[key],
                target=asdict(image.target),
                observed=image.observed,
                coordinates=image.coordinates,
                capture=capture_metadata(image.image),
            )
            for key, image in images
        )
        return ImageHeader(
            kind,
            tuple(dict.fromkeys(aliases[key] for key in displayed if aliases[key] is not None)),
            not canvas.unavailable_mapping and any(confirmed[key] for key in displayed),
            captures,
            capture_metadata(canvas.png),
            tuple((piece.window, piece.box) for piece in canvas.pieces),
            canvas.origin,
            canvas.scale,
            canvas.membership,
        )

    def register(
        self, ui: UIHandle, canvas: InterfaceImage, target: Binding, summary: ImageHeader,
        *, historical: tuple[DisplayText, ...], has_confirmed_pixels: bool,
    ) -> None:
        """Retain the composite image's source windows and coordinate mapping.

        Args:
            ui: App or request scope whose instance and observation bounds constrain the
                operation.
            target: A captured source identity whose native session owns this image.
            summary: Exact displayed window identities and capture-time evidence.
        """
        adapters = self.controller.adapters
        canvas.digest = hashlib.sha256(canvas.png).digest()
        # pylint: disable-next=protected-access
        state = ui._instance
        # UIHandle keeps the fixed observation scope separate from App state.
        # pylint: disable-next=protected-access
        fixed_window = ui._anchor is not None
        previous = (
            self.controller.interfaces.windows.delivered_image(state, ui) if fixed_window else state.delivered_image
        )
        state.image_generation += 1
        generation = state.image_generation
        canvas.generation = generation
        for piece in canvas.pieces:
            if not piece.scope_confirmed:
                continue
            identity = WindowIdentity(piece.window, piece.image.target.surface, piece.image.target.instance)
            state.sampled_window_images[identity] = canvas
        self.controller.port.capture_references = self.controller.interfaces.windows.capture_references()

        def sources(value: Any) -> tuple[Any, ...]:
            return tuple(
                (piece.window, replace(piece.image.target, capture=None), piece.box, piece.scope_confirmed)
                for piece in value.pieces
            )

        matches_previous = (
            True
            if (
                previous is not None
                and not previous.expired
                and not previous.unavailable_mapping
                and previous.delivered == adapters.image_delivery_epoch()
                and previous.digest == canvas.digest
                and previous.membership == canvas.membership
                and previous.content_hosts == canvas.content_hosts
                and previous.shared_pixels == canvas.shared_pixels
                and previous.identities == canvas.identities
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
            return not (
                image is None
                or native is None
                or state is None
                or image.expired
                or native.transport.session != session
                or hashlib.sha256(image.png).digest() != image.digest
            )

        def coordinate_windows() -> tuple[int, ...]:
            if not valid():
                return ()
            image, state = value(), application()
            identities = {
                piece.window: (piece.image.target.surface, piece.image.target.instance) for piece in image.pieces
                if piece.scope_confirmed
            }
            captured = {identity.window: identity for identity in image.identities}
            hosts = dict(image.content_hosts)
            captured_hosts = {key: {"content": {"host": hosts.get(key)}} for key in captured}
            for key in tuple(identities):
                host = display_host(captured_hosts, key)
                original = captured.get(host)
                member = state.members.get(host)
                if host == key or original is None or member is None:
                    continue
                if WindowIdentity.of(member) == original and image.for_window(host) is not None:
                    identities[host] = (original.surface, original.instance)
                    identities.pop(key)
            available = []
            for ref, window in state.windows.items():
                if window not in identities:
                    continue
                surface, instance = identities[window]
                identity = WindowIdentity(window, surface, instance)
                evidence = state.evidence.get(identity)
                member = state.members.get(window)
                retained = evidence is not None and evidence.lifetime is not WindowLifetime.RETIRED
                captured_member = member is not None and WindowIdentity.of(member) == identity
                if not retained and not (evidence is None and captured_member):
                    continue
                if generation >= state.window_image_generations.get(identity, -1):
                    available.append(ref)
            return tuple(available)

        def current() -> bool:
            image = value()
            return valid() and (
                image.unavailable_mapping and has_confirmed_pixels or bool(coordinate_windows())
            )

        # UIHandle keeps the fixed observation scope separate from App state.
        # pylint: disable-next=protected-access
        fixed_ref = next((ref for ref, key in state.windows.items() if key == ui._anchor), None)
        application_target = "computer.app(" + repr(str(state.selector)) + ")"
        regions = ()
        if len(canvas.pieces) > 1 and not historical:
            boxes = {piece.window: piece.box for piece in canvas.pieces if piece.scope_confirmed}
            regions = tuple((ref, boxes[key]) for ref, key in state.windows.items() if key in boxes)

        def coordinate_target() -> str | None:
            # An older popup/composite can still be valid for one Window even
            # after App coordinates advance to a newer delivered parent image.
            image, state = value(), application()
            if not current() or image.unavailable_mapping:
                return None
            if fixed_window:
                return (application_target + f" (window={fixed_ref})") if fixed_ref in coordinate_windows() else None
            return application_target if generation >= state.delivered_image_generation else None

        def delivered(coordinates: ImageCoordinates | None = None) -> None:
            if current():
                image, state, native = value(), application(), controller()
                image.delivered = adapters.image_delivery_epoch()
                image.delivered_coordinates = coordinates
                if generation > state.delivered_image_generation:
                    state.delivered_image = image
                    state.delivered_image_generation = generation
                native.interfaces.windows.retain_image(state, image, generation=generation)
                native.port.capture_references = native.interfaces.windows.capture_references()

        adapters.register_image(
            canvas.png,
            dict(
                kind="window",
                windows=summary.windows,
                windowStateNotes=image_state_notes(summary),
                mappingAvailable=not canvas.unavailable_mapping and has_confirmed_pixels,
                historical=bool(historical),
                # pylint: disable-next=protected-access
                selector=str(ui._instance.selector),
                coordinateOwner=application,
                coordinateGeneration=generation,
                coordinateTarget=coordinate_target,
                coordinateRegions=regions,
                context=join((template(
                    "App {}; {}",
                    # pylint: disable-next=protected-access
                    ui._instance.name or str(ui._instance.selector),
                    # pylint: disable-next=protected-access
                    "observed windows" if ui._anchor is None else "selected window",
                ), *historical)),
                current=current,
                delivered=delivered,
                coordinate_windows=coordinate_windows,
                coordinateScope=(
                    "window", session,
                    tuple(
                        (piece.window, piece.image.target.instance, piece.image.target.surface, piece.box,
                         capture_metadata(piece.image.image)["width"], capture_metadata(piece.image.image)["height"],
                         piece.scope_confirmed)
                        for piece in canvas.pieces
                    ),
                ) if canvas.pieces else None,
            ),
        )
