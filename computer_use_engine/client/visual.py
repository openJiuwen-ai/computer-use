# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public handles for frame-scoped visual references."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.identity import FrozenRecord

from ..visual.model import VisualElement as VisualElementRecord
from .execution import synchronous
from .handles import handle_created

if TYPE_CHECKING:
    from .computer import App

_MISSING = object()


class VisualElement:
    """A pointer-addressable visual region owned by one App handle.

    Visual references carry no accessibility identity or keyboard receiver. They
    support pointer actions and return the recognized text as evidence only.
    """

    def __init__(
        self, controller=None, owner: App | None = None, element=None, *, window=None, _internal=False,
    ) -> None:
        if not _internal:
            raise TypeError("VisualElement requires an App visual binding")
        if not isinstance(element, VisualElementRecord):
            raise TypeError("VisualElement requires captured visual evidence")
        from .computer import executor_for_controller

        self._controller = controller
        self._owner = owner
        self._element = element
        self._window = window
        self._executor = executor_for_controller(controller)
        handle_created(
            self,
            {"kind": "VisualElement", "controller": controller, "owner": owner, "element": element},
        )

    @property
    def ref(self) -> int:
        return self._element.ref

    @property
    def label(self) -> str:
        return self._element.label

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return self._element.rect

    @property
    def confidence(self) -> float:
        return self._element.confidence

    def _require_current_frame(self):
        """Reject a handle from an earlier capture instead of retargeting it."""
        current = getattr(self._owner, "_visual_frame", _MISSING)
        if current is _MISSING:
            return
        if current == self._element.frame_id:
            return
        index = getattr(self._owner, "_visual", None)
        elements = getattr(index, "elements", ())
        hints = ", ".join(f"v{item.ref} {item.label!r}" for item in elements[:8])
        detail = (
            f"visual reference belongs to frame {self._element.frame_id or 'unknown'}; "
            f"current frame is {current or 'none'}. Read again for fresh evidence."
        )
        if hints:
            detail += f" Current elements: {hints}."
        raise local_failure(detail, "stale_frame")

    @property
    def _center_delivered(self):
        center = self._element.center
        state = getattr(self._controller, "state", None)
        if state is None:
            return center
        try:
            coordinates = state(self._owner).delivered_coordinates
        except Exception:  # noqa: BLE001 - fake controllers and unobserved owners fall back to the source image
            return center
        if coordinates is None:
            return center
        return coordinates.delivered_point(center)

    @property
    def target(self):
        """Retain the region center in the owner's delivered image."""
        self._require_current_frame()
        return self._owner.point(self._center_delivered, window=self._window)

    async def _point_action(self, intent, args):
        from computer_use.interaction.agent.scope import request_scope

        from . import parameters
        from .targets import image_target, target_request

        self._require_current_frame()
        owner = await request_scope(self._controller, self._owner, self._window)
        point = parameters.point(self._center_delivered)
        try:
            target = image_target(self._controller, owner, point)
        except ComputerUseError as error:
            if "image mapping" in (error.detail or ""):
                raise local_failure(
                    "visual reference requires a delivered image; read in one call, then act in a later call",
                    "stale_target",
                ) from error
            raise
        request = target_request(self._controller, target, intent, args, owner=owner)
        return await self._controller.run(request)

    @synchronous
    async def click(self, options=None):
        from . import parameters

        return await self._point_action("click", parameters.click(options))

    @synchronous
    async def scroll(self, direction, *, pages=1):
        from . import parameters

        parameters.scroll(direction, pages)
        return await self._point_action("scroll", {"direction": direction, "pages": pages})

    @synchronous
    async def read_text(self, *, length=4096):
        # Visual text is captured evidence, not a live accessibility subtree.
        # pylint: disable-next=unidiomatic-typecheck
        if type(length) is not int or not 1 <= length <= 16384:
            raise local_failure("length must be an integer in 1-16384 code points")
        label = self._element.label
        return FrozenRecord({"text": label[:length], "total": len(label), "next": None})

    @staticmethod
    def type_text(text, *, replace=False):
        raise local_failure(
            "visual reference has no keyboard receiver; click it and use the AX element if one appears",
            "input_unavailable",
        )

    @staticmethod
    def paste(text):
        raise local_failure(
            "visual reference has no keyboard receiver; click it and use the AX element if one appears",
            "input_unavailable",
        )

    @staticmethod
    def press_key(key):
        raise local_failure(
            "visual reference has no keyboard receiver; click it and use the AX element if one appears",
            "input_unavailable",
        )

    @staticmethod
    def focus():
        raise local_failure("visual reference has no accessibility focus", "input_unavailable")

    @staticmethod
    def open_menu():
        raise local_failure("visual reference has no accessible menu action", "input_unavailable")

    @staticmethod
    def perform_action(action):
        raise local_failure("visual reference has no accessible actions", "input_unavailable")
