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
from computer_use.session.interface_images import signature
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.surface_content import display_host
from computer_use.session.windows import WindowReferences

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.records import UIHandle
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult, Point


from .membership import compatible_membership


def note_pending_point_image(controller, ui, error, *, after=None):
    """Retain an exact pending image for delivery feedback only."""
    binding = getattr(ui, "_window_identity", None)
    if binding is None or not controller.adapters.image_delivery_required():
        return
    # UIHandle exposes the separate instance catalogue to internal pipelines.
    # pylint: disable-next=protected-access
    state = ui._instance
    identity = WindowIdentity(binding.root_window, binding.surface, binding.instance)
    pending = state.sampled_window_images.get(identity)
    if pending is None or pending.delivered is not None:
        return
    if after is not None and pending.generation <= after:
        return
    ref = next((ref for ref, window in state.windows.items() if window == identity.window), None)
    if ref is not None:
        # Host-only failure metadata retains exact pending pixels for delivery feedback.
        # pylint: disable-next=protected-access
        error._pending_point_image = (pending.png, ref)


def input_image(controller, ui, image=None, *, current=True):
    """Resolve and validate one delivered canvas without reading the desktop."""
    if current:
        validate_session(controller, ui)
    if image is None:
        # UIHandle keeps the fixed observation scope separate from App state.
        # pylint: disable-next=protected-access
        fixed_window = ui._anchor is not None
        # UIHandle exposes the separate instance catalogue to internal pipelines.
        # pylint: disable-next=protected-access
        instance = ui._instance
        image = WindowReferences.delivered_image(instance, ui) if fixed_window else instance.delivered_image
    adapters = controller.adapters
    image_invalid = image is not None and current and (
        image.expired or hashlib.sha256(image.png).digest() != image.digest
    )
    if image is None or image_invalid:
        error = local_failure("this UI has no current image mapping", "snapshot_expired")
        if image is None and not current:
            note_pending_point_image(controller, ui, error)
        raise error
    if current and adapters.image_delivery_required() and image.delivered != adapters.image_delivery_epoch():
        raise local_failure("this image has not been delivered to the model", "input_unavailable")
    return image


class InterfacePointerPipeline:
    """Execute a gesture in an App or Window screenshot's coordinate space.

    Check the delivered image and current window membership, then map the gesture
    to its captured native member window. Native dispatch validates the original
    map against current geometry, then returns that window's action result.

    Attributes:
        controller: Supplies current UI membership and captured windows.
        write: Shared action flow composed after coordinate validation.
    """

    def __init__(self, controller: SessionController, write: Step[ExecuteAction, ActionResult]) -> None:
        self.controller = controller
        self.write = write

    async def run(
        self,
        ui: UIHandle,
        intent: str,
        points: tuple[Point, ...],
        options: Record,
        control: Control,
        *,
        global_input=False,
        image=None,
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
        image = input_image(self.controller, ui, image)
        mapped = tuple(image.hit(point) for point in points)
        piece = mapped[0][0]
        if any(other.window != piece.window for other, point in mapped):
            raise local_failure(
                "a UI drag must start and end in one captured member window",
                "unsupported_operation",
            )
        # This gesture addresses a retained image, not provider controls. Query
        # fresh window/membership facts; native dispatch still validates the
        # original capture, identity, modality and physical target boundary.
        await refresh(self.controller, ui, accessibility=False)
        if not compatible_membership(image.membership, signature(allowed(ui)), piece.window):
            image.expired = True
            raise local_failure("the original window binding changed", "snapshot_expired")
        if intent == "drag":
            start, end = mapped[0][1], mapped[1][1]
            parameters = dict(fromX=start[0], fromY=start[1], toX=end[0], toY=end[1])
        else:
            x, y = mapped[0][1]
            parameters = dict(x=x, y=y, **options)
        # The model may have received the popup pixels under its document's
        # public alias. Carry that captured display identity to the receipt,
        # independently of the physical popup binding used for every input.
        captured = {identity.window: identity for identity in image.identities}
        hosts = dict(image.content_hosts)
        host = display_host({key: {"content": {"host": hosts.get(key)}} for key in captured}, piece.window)
        display_window = captured.get(host) if host != piece.window else None
        return await self.write.arun(
            ExecuteAction(ui, Intent(intent, parameters, global_input), piece.image.target,
                          display_window=display_window), control
        )
