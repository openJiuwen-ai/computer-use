# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate one confirmed UI-image gesture to one retained native capture."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.query.instances.scope import allowed, refresh, validate_session
from computer_use.interaction.requests import ExecuteAction
from computer_use.native.contracts import Intent
from computer_use.session.interface_images import frame, signature

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.records import UIHandle
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record
    from computer_use.session.interface_images import InterfaceImage
    from computer_use.types import ActionResult, Point


from .membership import compatible_membership


class InterfacePointerPipeline:
    """Execute a gesture in an App or Window screenshot's coordinate space.

    Check the delivered image and current window membership, then map the gesture
    to its captured native member window. Revalidate composite-image layout
    before dispatch, then return the mapped window's action result.

    Attributes:
        controller: Supplies current UI membership and captured windows.
        write: Shared action flow composed after coordinate validation.
    """

    def __init__(self, controller: SessionController, write: Step[ExecuteAction, ActionResult]) -> None:
        self.controller = controller
        self.write = write

    async def run(
        self, ui: UIHandle, intent: str, points: tuple[Point, ...], options: Record, control: Control
    ) -> ActionResult:
        """Validate the delivered image mapping and execute the gesture on its native window.

        Args:
            ui: App or Window whose current image defines the coordinate space.
            intent: Pointer operation, such as click or drag.
            points: Gesture points in delivered-image pixels, from the top-left origin.
            options: Mouse button, click count, and other validated gesture options.
            control: Parent execution metadata passed to the write child.

        Returns:
            The action receipt for the mapped member window.
        """
        validate_session(self.controller, ui)
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        image = ui._image
        if image is None or image.expired or hashlib.sha256(image.png).digest() != image.digest:
            raise local_failure(
                "this UI has no current image mapping",
                'read and inspect this App/Window with view="image" or "both"',
                "snapshot_expired",
            )
        adapters = self.controller.adapters
        if adapters.image_delivery_required() and image.delivered != adapters.image_delivery_epoch():
            raise local_failure(
                "this image has not been delivered to the model",
                "return the screenshot and choose coordinates in the next call",
                "input_unavailable",
            )
        mapped = tuple(image.hit(point) for point in points)
        piece = mapped[0][0]
        if any(other.window != piece.window for other, point in mapped):
            raise local_failure(
                "a UI drag must start and end in one captured member window",
                "use an authorized exclusive desktop image for a cross-window drag",
                "mode_unsupported",
            )
        # This gesture addresses a retained image, not provider controls. Query
        # fresh window/membership facts; native dispatch still validates the
        # original capture, identity, modality and physical target boundary.
        await refresh(self.controller, ui, accessibility=False)
        if not compatible_membership(image.membership, signature(allowed(ui))):
            image.expired = True
            raise local_failure(
                "the window membership or modal layout changed", "read this UI image again", "snapshot_expired"
            )
        if len(image.pieces) > 1:
            await self.check_layout(image)
        if intent == "drag":
            start, end = mapped[0][1], mapped[1][1]
            parameters = dict(fromX=start[0], fromY=start[1], toX=end[0], toY=end[1])
        else:
            x, y = mapped[0][1]
            parameters = dict(x=x, y=y, **options)
        return await self.write.arun(ExecuteAction(ui, Intent(intent, parameters), piece.image.target), control)

    async def check_layout(self, image: InterfaceImage) -> None:
        """Require captured member windows to retain their size and relative positions."""
        shifts = []
        for piece in image.pieces:
            current = await self.controller.reader.images.window(piece.image.target)
            old, new = frame(piece.image), frame(current)
            if any(abs(a - b) > 1e-6 for a, b in zip(old[2:], new[2:])):
                image.expired = True
                raise local_failure("a member window changed size", "read this UI image again", "snapshot_expired")
            shifts.append((new[0] - old[0], new[1] - old[1]))
        for shift in shifts[1:]:
            if any(abs(a - b) > 1e-6 for a, b in zip(shifts[0], shift)):
                image.expired = True
                raise local_failure("relative window positions changed", "read this UI image again", "snapshot_expired")
