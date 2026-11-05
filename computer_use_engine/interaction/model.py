# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Callbacks that convert session results into public SDK values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.protocol.png import PNG
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult, Observation, TextPage


@dataclass(frozen=True)
class ControllerAdapters:
    """Host-supplied constructors and image-delivery hooks for a session.

    Attributes:
        observation_state: Construct an Observation from the assembled response fields.
        action_result: Construct an ActionResult from the native execution outcome.
        captured_text: Construct a TextPage from captured text and continuation metadata.
        register_image: Register PNG bytes and their observation/delivery callbacks.
        image_delivery_epoch: Return the current image-delivery generation.
        image_delivery_required: Report whether coordinate input requires image delivery.
    """

    observation_state: Callable[[Record], Observation]
    action_result: Callable[..., ActionResult]
    captured_text: Callable[..., TextPage]
    register_image: Callable[[PNG, Record], None]
    image_delivery_epoch: Callable[[], int]
    image_delivery_required: Callable[[], bool]
