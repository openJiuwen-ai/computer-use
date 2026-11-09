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
    retain_delivered_point: Callable[[], bool] | None = None


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
        self._view = state
        if not self._retains_delivered_point():
            self._delivered = None
        png = state.get("screenshot")
        if png is not None:
            # Registry callbacks must not retain their own image or owner.
            image, desktop = weakref.ref(png), weakref.ref(self)
            self.images.register_image(
                png,
                dict(
                    kind="desktop",
                    coordinateScope=("desktop", desktop),
                    current=lambda: desktop() is not None and image() is not None and desktop().current(image()),
                    delivered=lambda coordinates=None: (
                        desktop().delivered(image(), coordinates)
                        if desktop() is not None and image() is not None
                        else None
                    ),
                ),
            )

    def current(self, png):
        return self._view is not None and self._view.get("screenshot") is png

    def observed(self, ref):
        if self._view is None or self._view["desktopRef"] != ref:
            raise local_failure("desktop observation changed", "stale_target", "Desktop")
        return self._view

    def delivered(self, png, coordinates=None):
        if self.current(png):
            self._delivered = dict(state=self._view, epoch=self.images.image_delivery_epoch(), coordinates=coordinates)

    def _retains_delivered_point(self):
        return (
            self.images.retain_delivered_point is not None
            and self.images.retain_delivered_point()
            and self.images.image_delivery_required()
        )

    def point(self, x, y):
        delivery = self._delivered
        if (
            delivery is not None
            and self._retains_delivered_point()
            and delivery["epoch"] == self.images.image_delivery_epoch()
        ):
            # A Windows write may buffer a newer capture inside one MCP call.
            # The caller's point still belongs to the last returned pixels.
            state = delivery["state"]
        else:
            self.target(True, current=False)
            state = self._view
        coordinates = delivery["coordinates"] if delivery is not None else None
        return state, self._check_point(state, coordinates, x, y)

    def map_parameters(self, operation, parameters, *, current=True):
        self.target(True, current=current)
        coordinates = self._delivered.get("coordinates") if self._delivered is not None else None
        return coordinates.source_parameters(operation, parameters) if coordinates is not None else parameters

    def target(self, coordinates=False, *, current=True):
        state = self._view
        if state is None:
            raise local_failure(
                "desktop observation is missing or expired",
                "stale_target",
                "Desktop",
            )
        if coordinates:
            if state.get("screenshot") is None:
                raise local_failure(
                    "desktop observation has no PNG",
                    "input_unavailable",
                    "Desktop",
                )
            invalid_state = (
                True
                if self.images.image_delivery_required()
                and (
                    self._delivered is None
                    or self._delivered["state"] is not state
                    or current and self._delivered["epoch"] != self.images.image_delivery_epoch()
                )
                else False
            )
            if invalid_state:
                raise local_failure(
                    "current desktop image has not been returned to the model",
                    "input_unavailable",
                    "Desktop",
                )
        return state["desktopRef"]

    def check_coordinates(self, x, y, *, current=True):
        self.target(True, current=current)
        coordinates = self._delivered.get("coordinates") if self._delivered is not None else None
        self._check_point(self._view, coordinates, x, y)

    @staticmethod
    def _check_point(state, coordinates, x, y):
        if coordinates is not None:
            x, y = coordinates.source_point((x, y))
        invalid_x = True if not finite(x) or not finite(y) or x < 0 or y < 0 else False
        if invalid_x:
            raise local_failure("coordinates must be finite, nonnegative numbers")

        def _iter_d(_source_items: Iterator[Any]) -> Iterator[Any]:
            for d in _source_items:
                if (
                    d["rect"]["x"] <= x < d["rect"]["x"] + d["rect"]["width"]
                    and d["rect"]["y"] <= y < d["rect"]["y"] + d["rect"]["height"]
                ):
                    yield d

        matches = list(_iter_d(iter(state["observation"]["displays"])))
        if len(matches) != 1 or not matches[0]["captured"]:
            raise local_failure(
                "coordinates are outside a captured display or in a display gap/overlap",
            )
        return x, y
