# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Callbacks that guard one XTEST pointer sequence and report its progress."""

from collections.abc import Callable
from dataclasses import dataclass


def _noop() -> None:
    """Leave an optional pointer sequence hook inactive."""


@dataclass(frozen=True)
class PointerCallbacks:
    validate: Callable[[], None] = _noop
    before_dispatch: Callable[[], None] = _noop
    validate_drag: Callable[[], None] | None = None
    scroll_step_complete: Callable[[], None] = _noop
    event_progress: Callable[..., None] | None = None
