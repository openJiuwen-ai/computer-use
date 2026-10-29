# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import weakref
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from computer_use.diagnostics import local_failure

from ..protocol.primitives import finite


@dataclass(frozen=True)
class DesktopImageAccess:
    register_image: Callable
    image_delivery_epoch: Callable
    image_delivery_required: Callable


class DesktopStore:
    def __init__(self, images: DesktopImageAccess):
        self.images = images
        self._view = None
        self._delivered = None
        self.reset()

    def reset(self):
        self._view = None
        self._delivered = None

    def record(self, state):
        self._view, self._delivered = state, None
        png = state.get("screenshot")
        if png is not None:
            # Registry callbacks must not retain their own image or owner.
            image, desktop = weakref.ref(png), weakref.ref(self)
            self.images.register_image(
                png,
                dict(
                    kind="desktop",
                    current=lambda: desktop() is not None and image() is not None and desktop().current(image()),
                    delivered=lambda: (
                        desktop().delivered(image()) if desktop() is not None and image() is not None else None
                    ),
                ),
            )

    def current(self, png):
        return self._view is not None and self._view.get("screenshot") is png

    def observed(self, ref):
        if self._view is None or self._view["desktopRef"] != ref:
            raise local_failure("desktop observation changed", "read the desktop again", "stale_target", "Desktop")
        return self._view

    def delivered(self, png):
        if self.current(png):
            self._delivered = dict(state=self._view, epoch=self.images.image_delivery_epoch())

    def target(self, coordinates=False):
        state = self._view
        if state is None:
            raise local_failure(
                "desktop observation is missing or expired",
                "call Computer.read before input",
                "stale_target",
                "Desktop",
            )
        if state["observation"]["mode"] != "exclusive":
            raise local_failure(
                "desktop input requires exclusive mode",
                "use App/Element operations in shared mode; desktop mode is host configuration",
                "mode_unsupported",
                "Desktop",
            )
        if coordinates:
            if state.get("screenshot") is None:
                raise local_failure(
                    "desktop observation has no PNG",
                    "read the desktop and inspect capture availability",
                    "input_unavailable",
                    "Desktop",
                )
            invalid_state = (
                True
                if self.images.image_delivery_required()
                and (
                    self._delivered is None
                    or self._delivered["state"] is not state
                    or self._delivered["epoch"] != self.images.image_delivery_epoch()
                )
                else False
            )
            if invalid_state:
                raise local_failure(
                    "current desktop image has not been returned to the model",
                    "finish Computer.read() in this tool call, then choose desktop coordinates in the next call",
                    "input_unavailable",
                    "Desktop",
                )
        return state["desktopRef"]

    def check_coordinates(self, x, y):
        self.target(True)
        invalid_x = True if not finite(x) or not finite(y) or x < 0 or y < 0 else False
        if invalid_x:
            raise local_failure(
                "coordinates must be finite, nonnegative numbers", "use pixels from Computer.read's desktop PNG"
            )

        def _iter_d(_source_items: Iterator[Any]) -> Iterator[Any]:
            for d in _source_items:
                if (
                    d["rect"]["x"] <= x < d["rect"]["x"] + d["rect"]["width"]
                    and d["rect"]["y"] <= y < d["rect"]["y"] + d["rect"]["height"]
                ):
                    yield d

        matches = list(_iter_d(iter(self._view["observation"]["displays"])))
        if len(matches) != 1 or not matches[0]["captured"]:
            raise local_failure(
                "coordinates are outside a captured display or in a display gap/overlap",
                "choose a point on one captured display in the desktop PNG",
            )
