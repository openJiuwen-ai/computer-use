# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable observations whose complete text is rendered at an output boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

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


@dataclass(frozen=True)
class ObservationPresentation:
    """One host view: its text geometry and PNG always share the same pixels."""

    document: ObservationDocument
    screenshot: Any


class CapturedObservationText(str):
    """Full text with its immutable capture for host-side presentation.

    Ordinary SDK text remains in source pixels. A host resizing the associated
    PNG binds a paired presentation before the SDK call returns, without parsing
    strings or changing the retained source facts and input identity.
    """

    def __new__(cls, text: str, observation: LazyObservation):
        value = super().__new__(cls, text)
        value.observation = observation
        return value

    def __getitem__(self, key):
        """Keep a text slice's capture identity without changing its string value.

        MCP can recognize a redundant observation print by provenance. Ordinary
        SDK consumers still receive the exact requested slice; indexing one
        character or converting to ``str`` produces an ordinary string.
        """
        value = super().__getitem__(key)
        return type(self)(value, self.observation) if isinstance(key, slice) else value


class LazyObservation(FrozenRecord):
    """Retain a captured document and cache its full text when explicitly requested.

    Reading image fields or retaining an observation leaves the document structured.
    Accessing ``text`` or displaying the result renders only this captured document.
    """

    __slots__ = ("_detail_pages", "_details_follow_presentation", "_document", "_presentation", "_rendered_text")

    def __init__(
        self,
        document: ObservationDocument,
        screenshot: Any = None,
        *,
        details_document: ObservationDocument | None = None,
        element_details: Callable[[int], ObservationDocument] | None = None,
        overview: Any = _UNSET,
        preparation: Mapping[str, Any] | None = None,
    ) -> None:
        values = dict(text=None, screenshot=screenshot, preparation=preparation)
        if overview is not _UNSET:
            values["overview"] = overview
        super().__init__(values)
        object.__setattr__(self, "_document", document)
        object.__setattr__(self, "_rendered_text", None)
        object.__setattr__(self, "_presentation", None)
        object.__setattr__(self, "_details_follow_presentation", details_document is None)
        object.__setattr__(
            self, "_detail_pages", CapturedDetailPages(details_document or document, element_details=element_details)
        )

    def __getitem__(self, key: str) -> Any:
        if key == "text":
            if self._rendered_text is None:
                text = (self._presentation.document if self._presentation is not None else self._document).text
                if not isinstance(text, str):
                    raise TypeError("observation document must render text")
                object.__setattr__(self, "_rendered_text", CapturedObservationText(text, self))
            return self._rendered_text
        if key == "screenshot" and self._presentation is not None:
            return self._presentation.screenshot
        return super().__getitem__(key)

    @property
    def source_screenshot(self):
        """Return the captured PNG retained as input-coordinate authority."""
        return self._data.get("screenshot")

    @property
    def visual(self):
        """Return frame-scoped visual references, or None when this read had none."""
        return self._data.get("visual")

    def with_visual(self, visual, marked=None):
        """Attach a visual reference table without changing captured source facts.

        ``source_screenshot`` stays the exact capture used for input mapping. The
        optional ``marked`` copy is display-only; when a presentation is already
        bound, its projected document is kept and its pixels are replaced by the
        marked copy so the host does not drop the numbered evidence.
        """
        from computer_use.interaction.read.presentation.document import DisplayDocument
        from computer_use.visual.render import render_legend

        legend = render_legend(visual)
        # Keep the visual table ahead of the accessibility text: a long tree can
        # otherwise hide the only addressable references from the reader.
        if isinstance(self._document, DisplayDocument):
            document = DisplayDocument.join((legend, self._document))
        else:
            document = CapturedTextDocument(f"{legend}\n{self._document.text}")
        clone = object.__new__(type(self))
        data = dict(self._data)
        data["visual"] = visual
        data["marked"] = marked
        object.__setattr__(clone, "_data", MappingProxyType(data))
        object.__setattr__(clone, "_display", self._display)
        object.__setattr__(clone, "_document", document)
        object.__setattr__(clone, "_detail_pages", self._detail_pages)
        object.__setattr__(clone, "_details_follow_presentation", self._details_follow_presentation)
        presentation = self._presentation
        if presentation is not None:
            # A bound presentation is what the host delivers. Keep its document
            # projection but replace the pixels with the marked display copy so
            # the numbered evidence is not silently dropped.
            presentation = ObservationPresentation(document, marked if marked is not None else presentation.screenshot)
        object.__setattr__(clone, "_presentation", presentation)
        object.__setattr__(clone, "_rendered_text", None)
        return clone

    def read_details(self, *, element: int | None = None, cursor: str | None = None, length: int = 4096) -> TextPage:
        """Page retained native facts and diagnostics from this observation.

        Default pages share this observation's text/image presentation. Explicit
        native detail documents retain their original screen/AX coordinate space.

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
    preparation: Mapping[str, Any] | None = None,
) -> LazyObservation:
    """Create a public observation without rendering the captured document."""
    return LazyObservation(
        document,
        screenshot,
        details_document=details_document,
        element_details=element_details,
        overview=overview,
        preparation=preparation,
    )


def peek_observation_text(state: Mapping[str, Any]) -> str | None:
    """Return already available full text without materializing a lazy observation."""
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    return state._rendered_text if isinstance(state, LazyObservation) else state.get("text")


def observation_document(state: Mapping[str, Any]) -> ObservationDocument | None:
    """Return the retained source document without rendering or reacquiring it."""
    # This module owns the immutable observation's presentation source.
    # pylint: disable-next=protected-access
    return state._document if isinstance(state, LazyObservation) else None


def observation_screenshot(state: Mapping[str, Any]) -> Any:
    """Return the source capture, independently of a host's paired presentation."""
    # pylint: disable-next=protected-access
    return state._data["screenshot"] if isinstance(state, LazyObservation) else state.get("screenshot")


def observation_marked(state: Mapping[str, Any]) -> Any:
    """Return the display-only marked copy, if the observation captured one."""
    return state.get("marked")


def observation_presentation(state: Mapping[str, Any]) -> ObservationPresentation | None:
    """Return the exact presentation already bound to this observation, if any."""
    # pylint: disable-next=protected-access
    return state._presentation if isinstance(state, LazyObservation) else None


def bind_observation_presentation(
    state: LazyObservation, document: ObservationDocument, screenshot: Any
) -> ObservationPresentation:
    """Bind the host's text/image pair before returning the SDK call to its user.

    This is a one-time delivery decision, not a change to native facts or capture
    identity. Every ordinary string operation and saved screenshot then describes
    the same view; later observations cannot reinterpret this retained result.
    """
    previous = observation_presentation(state)
    if previous is not None:
        return previous
    presentation = ObservationPresentation(document, screenshot)
    object.__setattr__(state, "_presentation", presentation)
    object.__setattr__(state, "_rendered_text", None)
    # Only the default presentation fallback follows image pixels. Explicit
    # native facts retain screen/AX geometry and are never display-transformed.
    # pylint: disable-next=protected-access
    if state._details_follow_presentation:
        # pylint: disable-next=protected-access
        object.__setattr__(state, "_detail_pages", state._detail_pages.with_document(document))
    return presentation


if TYPE_CHECKING:
    from computer_use.types import TextPage
