# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable visual-element values derived from captured image evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VisualSource = Literal["windowsOCR", "macOSVision"]


@dataclass(frozen=True, kw_only=True)
class VisualElement:
    """One captured region addressed by a frame-scoped visual reference.

    Attributes:
        ref: Positive display reference, allocated in reading order within a frame.
        label: Recognized text used only as evidence for this region.
        rect: Source PNG pixels as ``(x, y, width, height)``.
        source: Provider that supplied the region.
        confidence: Provider confidence when available; 1.0 for exact OCR lines.
        frame_id: Capture identity this reference belongs to.
    """

    ref: int
    label: str
    rect: tuple[float, float, float, float]
    source: VisualSource = "windowsOCR"
    confidence: float = 1.0
    frame_id: str = ""

    def __post_init__(self) -> None:
        if self.ref < 1:
            raise ValueError("visual reference must be positive")
        _x, _y, width, height = self.rect
        if width <= 0 or height <= 0:
            raise ValueError("visual rectangle must have positive size")
        if not 0 <= self.confidence <= 1:
            raise ValueError("visual confidence must be within 0..1")

    @property
    def center(self) -> tuple[float, float]:
        """Return the source-image pixel at the region's center."""
        x, y, width, height = self.rect
        return x + width / 2, y + height / 2


@dataclass(frozen=True, kw_only=True)
class VisualIndex:
    """Visual references captured from one image frame.

    Attributes:
        frame_id: Capture identity shared by every element.
        elements: Visual-only elements in reading order.
        truncated: Whether the provider or this index omitted eligible regions.
        source: Provider that supplied the captured regions.
    """

    frame_id: str
    elements: tuple[VisualElement, ...]
    truncated: bool = False
    source: VisualSource = "windowsOCR"
    detail: str = ""

    def element(self, ref: int) -> VisualElement | None:
        return next((item for item in self.elements if item.ref == ref), None)


@dataclass(frozen=True)
class VisualRef:
    """A frame-scoped reference accepted by ``App.element``."""

    ref: int

    def __post_init__(self) -> None:
        if self.ref < 1:
            raise ValueError("visual reference must be positive")

# Public shape name used by the typing stub and observation consumers.
VisualElementInfo = VisualElement
