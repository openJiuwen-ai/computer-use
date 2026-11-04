# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local, observation-owned pages of a frozen diagnostic document."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from computer_use.identity import FrozenRecord
from computer_use.diagnostics import local_failure

if TYPE_CHECKING:
    from computer_use.session.observation import ObservationDocument
    from computer_use.types import TextPage


class CapturedDetailText(str):
    """Exact page characters tagged for host delivery, separate from observation text."""


class CapturedDetailPage(FrozenRecord):
    """A local diagnostic page whose display does not deliver a new observation."""


class CapturedDetailPages:
    """Render one retained capture on demand and page it without contacting the driver.

    Cursors belong to this object, remain reusable, and retain positions in Python
    characters. Rendering and paging neither publish observations nor deliver a baseline.
    """

    def __init__(
        self, document: ObservationDocument, *, element_details: Callable[[int], ObservationDocument] | None = None
    ) -> None:
        self.document = document
        self._element_details = element_details
        self._texts: dict[int | None, str] = {}
        self._positions: dict[str, tuple[int | None, int]] = {}
        self._cursors: dict[tuple[int | None, int], str] = {}

    def read(self, *, element: int | None = None, cursor: str | None = None, length: int = 4096) -> TextPage:
        """Return a bounded page from this capture, rendering it once on first access.

        Args:
            element: Captured element number, or None for all details or the cursor's scope.
            cursor: Opaque continuation returned by this observation and element scope.
            length: Maximum Python characters, from 1 through 16384.
        """
        if element is not None and (type(element) is not int or element < 0):
            raise local_failure(
                "details element must be a nonnegative integer", "use an element number from this observation"
            )
        if type(length) is not int or not 1 <= length <= 16384:
            raise local_failure("details length must be an integer from 1 to 16384", "use read_details(length=4096)")
        if cursor is not None and (not isinstance(cursor, str) or cursor not in self._positions):
            raise local_failure(
                "details cursor does not belong to this observation",
                "reuse the observation that returned this cursor, or omit cursor",
            )
        start = 0
        if cursor is not None:
            selected, start = self._positions[cursor]
            if element is not None and element != selected:
                raise local_failure(
                    "details cursor belongs to another element scope",
                    "continue without element, or restart with cursor=None",
                )
            element = selected
        if element not in self._texts:
            if element is not None and self._element_details is None:
                raise local_failure(
                    "this observation has no captured element details",
                    "omit element to inspect its captured observation details",
                )
            document = self.document if element is None else self._element_details(element)
            value = document.text
            if not isinstance(value, str):
                raise TypeError("observation details must render text")
            self._texts[element] = "Captured details (same observation):\n" + value
        text = self._texts[element]
        end = min(len(text), start + length)
        following = None
        if end < len(text):
            position = (element, end)
            if position not in self._cursors:
                self._cursors[position] = "details_" + uuid4().hex
                self._positions[self._cursors[position]] = position
            following = self._cursors[position]
        data = dict(text=CapturedDetailText(text[start:end]), total=len(text), next=following)
        scope = "" if element is None else f", element {element}"
        return cast(
            "TextPage",
            CapturedDetailPage(
                data,
                display=(
                    f"Captured details [{start}:{end}] of {len(text)} characters (same observation{scope})\n"
                    + repr(data)
                ),
            ),
        )
