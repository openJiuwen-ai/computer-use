# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable observations whose complete text is rendered at an output boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from computer_use.identity import FrozenRecord
from computer_use.session.detail_pages import CapturedDetailPages

_UNSET = object()


class ObservationDocument(Protocol):
    """The frozen presentation needed to produce this capture's complete text."""

    @property
    def text(self) -> str:
        pass


@dataclass(frozen=True)
class CapturedTextDocument:
    """Already-rendered text retained by a local observation adapter."""

    text: str


class LazyObservation(FrozenRecord):
    """Retain a captured document and cache its full text when explicitly requested.

    Reading image fields or retaining an observation leaves the document structured.
    Accessing ``text`` or displaying the result renders only this captured document.
    """

    __slots__ = ("_document", "_rendered_text", "_detail_pages")

    def __init__(
        self,
        document: ObservationDocument,
        screenshot: Any = None,
        *,
        details_document: ObservationDocument | None = None,
        element_details: Callable[[int], ObservationDocument] | None = None,
        overview: Any = _UNSET,
    ) -> None:
        values = dict(text=None, screenshot=screenshot)
        if overview is not _UNSET:
            values["overview"] = overview
        super().__init__(values)
        object.__setattr__(self, "_document", document)
        object.__setattr__(self, "_rendered_text", None)
        object.__setattr__(
            self, "_detail_pages", CapturedDetailPages(details_document or document, element_details=element_details)
        )

    def __getitem__(self, key: str) -> Any:
        if key == "text":
            if self._rendered_text is None:
                text = self._document.text
                if not isinstance(text, str):
                    raise TypeError("observation document must render text")
                object.__setattr__(self, "_rendered_text", text)
            return self._rendered_text
        return super().__getitem__(key)

    async def read_details(
        self, *, element: int | None = None, cursor: str | None = None, length: int = 4096
    ) -> TextPage:
        """Page retained native facts and diagnostics from this observation.

        Args:
            element: Element number in this observation, or None for all captured details.
                When continuing a cursor, None retains that cursor's element scope.
            cursor: Continuation from this observation and element scope, or None to restart.
            length: Maximum Python characters to return, from 1 through 16384.

        Returns:
            Captured text, its character count, and an optional next cursor. No native
            read, observation event, or delivery-baseline update occurs.
        """
        return self._detail_pages.read(element=element, cursor=cursor, length=length)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return self["text"]


def lazy_observation(
    document: ObservationDocument,
    screenshot: Any = None,
    *,
    details_document: ObservationDocument | None = None,
    element_details: Callable[[int], ObservationDocument] | None = None,
    overview: Any = _UNSET,
) -> LazyObservation:
    """Create a public observation without rendering the captured document."""
    return LazyObservation(
        document, screenshot, details_document=details_document, element_details=element_details, overview=overview
    )


def peek_observation_text(state: Mapping[str, Any]) -> str | None:
    """Return already available full text without materializing a lazy observation."""
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    return state._rendered_text if isinstance(state, LazyObservation) else state.get("text")


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.types import TextPage
