"""Callbacks that convert session results into public SDK values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.png import PNG
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult, Observation, TextPage


@dataclass(frozen=True)
class ControllerAdapters:
    """Host-supplied constructors and image-delivery hooks for a session.

    Attributes:
        create_app: Construct a public App handle from its controller and native scope.
        observation_state: Construct an Observation from the assembled response fields.
        action_result: Construct an ActionResult from the native execution outcome.
        captured_text: Construct a TextPage from captured text and continuation metadata.
        register_image: Register PNG bytes and their observation/delivery callbacks.
        image_delivery_epoch: Return the current image-delivery generation.
        image_delivery_required: Report whether coordinate input requires image delivery.
        create_window: Construct a Window bound to an App and native window key,
            when the host exposes Window handles.
    """

    create_app: Callable[[SessionController, Record], UIHandle]
    observation_state: Callable[[Record], Observation]
    action_result: Callable[[Record], ActionResult]
    captured_text: Callable[..., TextPage]
    register_image: Callable[[PNG, Record], None]
    image_delivery_epoch: Callable[[], int]
    image_delivery_required: Callable[[], bool]
    create_window: Callable[[SessionController, Record, UIHandle, str], UIHandle] | None = None
