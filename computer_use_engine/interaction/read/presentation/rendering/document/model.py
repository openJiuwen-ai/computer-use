# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inputs to final snapshot rendering and text-only document assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.document import DisplayDocument

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan, RustDeltaPlan
    from computer_use.interaction.read.presentation.records import RenderedTree


class RustRenderedTree(Mapping[str, str]):
    """Expose only final text fields while retaining structural lines in Rust."""

    def __init__(self, handle: object, includes_details: bool) -> None:
        self.handle = handle
        self.includes_details = includes_details

    @cached_property
    def _text(self) -> str:
        return self.handle.text()

    @cached_property
    def _details(self) -> str:
        return self.handle.details()

    @cached_property
    def _supplement(self) -> str | None:
        return self.handle.detail_supplement()

    @cached_property
    def _directory(self) -> tuple[str, str] | None:
        return self.handle.content_directory()

    def __getitem__(self, key: str) -> str:
        if key == "text":
            return self._text
        if key == "details" and self.includes_details:
            return self._details
        if key == "detailSupplement" and self.includes_details and self._supplement is not None:
            return self._supplement
        if key in ("capturedContentDirectory", "capturedContentPayload") and self._directory is not None:
            return self._directory[key == "capturedContentPayload"]
        raise KeyError(key)

    def __iter__(self):
        yield "text"
        if self.includes_details:
            yield "details"
            if self._supplement is not None:
                yield "detailSupplement"
        if self._directory is not None:
            yield "capturedContentDirectory"
            yield "capturedContentPayload"

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def title_evidence(self) -> tuple[str, str, str] | None:
        return self.handle.title_evidence()


@dataclass(frozen=True)
class DocumentRenderRequest:
    """Captured document and its previously chosen structural delivery scope.

    Attributes:
        document: Immutable sections and prepared snapshots to render for this delivery.
        plan: Selected full or incremental scope; None requests the complete document.
        public_api: Whether generated continuation hints use Python API call syntax.
    """

    document: DisplayDocument
    plan: DeltaPlan | RustDeltaPlan | None = None
    public_api: bool = False
    prepared: RenderedDocumentInput | None = None


@dataclass(frozen=True)
class RenderedDocumentInput:
    """Formatting request with the snapshots already rendered by its parent flow.

    Attributes:
        request: Captured document, delivery decision, and continuation-hint convention.
        snapshots: Final renders indexed by the identity of each retained prepared snapshot.
    """

    request: DocumentRenderRequest
    snapshots: Mapping[int, RenderedTree | RustRenderedTree]
