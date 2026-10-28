"""Callbacks that guard one XTEST pointer sequence and report its progress."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PointerCallbacks:
    validate: Callable[[], None] = lambda: None
    before_dispatch: Callable[[], None] = lambda: None
    validate_drag: Callable[[], None] | None = None
    scroll_step_complete: Callable[[], None] = lambda: None
    event_progress: Callable[..., None] | None = None
