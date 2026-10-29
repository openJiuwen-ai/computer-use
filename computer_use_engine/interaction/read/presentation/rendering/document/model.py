# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inputs to final snapshot rendering and text-only document assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.document import DisplayDocument

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan
    from computer_use.interaction.read.presentation.records import RenderedTree


@dataclass(frozen=True)
class DocumentRenderRequest:
    """Captured document and its previously chosen structural delivery scope.

    Attributes:
        document: Immutable sections and prepared snapshots to render for this delivery.
        plan: Selected full or incremental scope; None requests the complete document.
        public_api: Whether generated continuation hints use Python API call syntax.
    """

    document: DisplayDocument
    plan: DeltaPlan | None = None
    public_api: bool = False


@dataclass(frozen=True)
class RenderedDocumentInput:
    """Formatting request with the snapshots already rendered by its parent flow.

    Attributes:
        request: Captured document, delivery decision, and continuation-hint convention.
        snapshots: Final renders indexed by the identity of each retained prepared snapshot.
    """

    request: DocumentRenderRequest
    snapshots: Mapping[int, RenderedTree]
