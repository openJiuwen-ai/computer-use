"""Immutable observations whose complete text is rendered at an output boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from computer_use._identity import FrozenRecord

_UNSET = object()


class ObservationDocument(Protocol):
    """The frozen presentation needed to produce this capture's complete text."""

    @property
    def text(self) -> str: ...


class LazyObservation(FrozenRecord):
    """Retain a captured document and cache its full text when explicitly requested.

    Reading image fields or retaining an observation leaves the document structured.
    Accessing ``text`` or displaying the result renders only this captured document.
    """

    __slots__ = ("_document", "_rendered_text", "_details_document", "_rendered_details")

    def __init__(
        self,
        document: ObservationDocument,
        screenshot: Any = None,
        *,
        details_document: ObservationDocument | None = None,
        overview: Any = _UNSET,
    ) -> None:
        values = dict(text=None, screenshot=screenshot)
        if details_document is not None:
            values["details"] = None
        if overview is not _UNSET:
            values["overview"] = overview
        super().__init__(values)
        object.__setattr__(self, "_document", document)
        object.__setattr__(self, "_rendered_text", None)
        object.__setattr__(self, "_details_document", details_document)
        object.__setattr__(self, "_rendered_details", None)

    def __getitem__(self, key: str) -> Any:
        if key == "text":
            if self._rendered_text is None:
                text = self._document.text
                if not isinstance(text, str):
                    raise TypeError("observation document must render text")
                object.__setattr__(self, "_rendered_text", text)
            return self._rendered_text
        if key == "details" and self._details_document is not None:
            if self._rendered_details is None:
                details = self._details_document.text
                if not isinstance(details, str):
                    raise TypeError("observation details must render text")
                object.__setattr__(self, "_rendered_details", details)
            return self._rendered_details
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return self["text"]


def lazyObservation(
    document: ObservationDocument,
    screenshot: Any = None,
    *,
    details_document: ObservationDocument | None = None,
    overview: Any = _UNSET,
) -> LazyObservation:
    """Create a public observation without rendering the captured document."""
    return LazyObservation(document, screenshot, details_document=details_document, overview=overview)


def peekObservationText(state: Mapping[str, Any]) -> str | None:
    """Return already available full text without materializing a lazy observation."""
    return state._rendered_text if isinstance(state, LazyObservation) else state.get("text")
