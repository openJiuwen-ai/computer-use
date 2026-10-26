"""Explicit terminal entry for formatting immutable display documents."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan
    from computer_use.interaction.read.presentation.document import DisplayDocument


def render_document(document: DisplayDocument, plan: DeltaPlan | None = None, *, public_api: bool = False) -> str:
    """Render retained snapshot projections and format their selected delivery scope."""
    from computer_use.interaction.read.presentation.rendering.document.model import DocumentRenderRequest
    from computer_use.interaction.read.presentation.rendering.document.pipeline import DocumentRenderingPipeline

    return DocumentRenderingPipeline().run(DocumentRenderRequest(document, plan, public_api))
