# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit terminal entry for formatting immutable display documents."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan
    from computer_use.interaction.read.presentation.document import DisplayDocument


def render_document(
    document: DisplayDocument, plan: DeltaPlan | None = None, *, public_api: bool = False, prepared=None
) -> str:
    """Render retained snapshot projections and format their selected delivery scope."""
    from computer_use.interaction.read.presentation.rendering.document.model import DocumentRenderRequest
    from computer_use.interaction.read.presentation.rendering.document.pipeline import DocumentRenderingPipeline

    return DocumentRenderingPipeline().run(DocumentRenderRequest(document, plan, public_api, prepared))


def prepare_document(document: DisplayDocument, *, public_api: bool = False, control=None):
    """Freeze complete compact source chunks without delivering or acknowledging them."""
    from computer_use.interaction.read.presentation.rendering.document.model import DocumentRenderRequest
    from computer_use.interaction.read.presentation.rendering.document.pipeline import PrepareDocumentRenderingPipeline

    return PrepareDocumentRenderingPipeline().run(DocumentRenderRequest(document, public_api=public_api), control)
