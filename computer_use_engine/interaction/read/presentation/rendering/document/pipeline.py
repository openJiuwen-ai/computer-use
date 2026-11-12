# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render prepared snapshot sources before the terminal text-assembly Pass."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.presentation.content import DisplayText, omit_public_details_hint
from computer_use.interaction.read.presentation.document import DisplayTree
from computer_use.interaction.read.accessibility.preparation import PURE
from computer_use.interaction.read.accessibility.preparation import PreparedSnapshot
from computer_use.interaction.read.presentation.rendering.document.model import (
    DocumentRenderRequest,
    RenderedDocumentInput,
    RustRenderedTree,
)
from computer_use.interaction.read.presentation.rendering.document.passes.format_document import (
    stage as format_document,
)
from computer_use.interaction.read.presentation.rendering.document.passes.plan_capture_context import (
    stage as plan_capture_context,
)
from computer_use.pipeline import Contract, Pass, Pipeline, Sequence
from computer_use.pipeline.core import Node

_rendered_sources = WeakIdentityMap()

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control, Step


def _snapshot_sources(document):
    """Collect exact source objects, including retained local detail hints."""
    pending, sources, details = list(reversed(document.parts)), {}, set()
    while pending:
        part = pending.pop()
        if isinstance(part, DisplayTree):
            sources.setdefault(id(part.prepared), part.prepared)
            if part.details:
                details.add(id(part.prepared))
        elif isinstance(part, DisplayText):
            if part.kind == "details_hint":
                source = part.values[0]
                sources.setdefault(id(source), source)
                if not omit_public_details_hint(source):
                    details.add(id(source))
            else:
                pending.extend(reversed(part.values))
        elif isinstance(part, (tuple, list)):
            pending.extend(reversed(part))
    return sources, details


def _render_native_roots(source: tuple[PreparedSnapshot, bool], control: Control) -> RustRenderedTree:
    """Render and account for retained native roots inside the original call."""
    prepared, include_details = source
    rendered = RustRenderedTree(prepared.handle.render(include_details), include_details)
    if control.remaining is not None:
        control.remaining()
    return rendered


_render_native_prepared_roots = Pass.define(
    name="RenderPreparedRoots",
    function=_render_native_roots,
    contract=Contract(tuple, RustRenderedTree, PURE),
)


class _RenderSnapshotSources(Node["DocumentRenderRequest", "RenderedDocumentInput"]):
    """Render each retained tree or detail-hint source once within final delivery.

    Attributes:
        presentation: Explicit child flow applying the existing field layout and source
            accounting to each already-compressed snapshot.
    """

    def __init__(self) -> None:
        self.presentation = Sequence(
            name="RenderPreparedSnapshotPipeline", steps=[_render_native_prepared_roots()]
        )
        self.children = (self.presentation,)
        super().__init__("RenderDocumentSnapshotSources", Contract(DocumentRenderRequest, RenderedDocumentInput, PURE))

    def _run(self, request: DocumentRenderRequest, control: Control) -> RenderedDocumentInput:
        sources, details = _snapshot_sources(request.document)
        if request.prepared is not None:
            # Capture-time preparation owns the source renders. Delivery may
            # choose a different delta after pending captures are coalesced;
            # it must only select already formatted nodes, never start another
            # complete renderer after the originating operation's deadline.
            original, _ = _snapshot_sources(request.prepared.request.document)
            if set(sources) != set(request.prepared.snapshots) or any(
                original.get(key) is not source for key, source in sources.items()
            ):
                raise ValueError("Prepared delivery does not belong to these exact captured sources")
            return RenderedDocumentInput(request, request.prepared.snapshots)
        snapshots = {}
        for key, source in sources.items():
            if control.remaining is not None:
                control.remaining()
            rendered = _rendered_sources.get(source)
            include_details = key in details
            if rendered is None or include_details and not rendered.includes_details:
                rendered = self.presentation.run((source, include_details), control)
                _rendered_sources.set(source, rendered)
            snapshots[key] = rendered
        return RenderedDocumentInput(request, MappingProxyType(snapshots))

    async def _arun(self, request: DocumentRenderRequest, control: Control) -> RenderedDocumentInput:
        return self._run(request, control)


class DocumentRenderingPipeline(Pipeline["DocumentRenderRequest", "str"]):
    """Render frozen snapshot sources and assemble the selected full or incremental text.

    Snapshot rendering is an explicit child flow. The final Pass only formats captured
    fields and selects already-rendered node lines using the structural delivery plan.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[DocumentRenderRequest, str]:
        return Sequence(
            name="DocumentRenderingPipeline",
            steps=[plan_capture_context(), _RenderSnapshotSources(), format_document()],
        )


class PrepareDocumentRenderingPipeline(Pipeline["DocumentRenderRequest", "RenderedDocumentInput"]):
    """Prepare baseline-independent source chunks within their originating call."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[DocumentRenderRequest, RenderedDocumentInput]:
        return Sequence(
            name="PrepareDocumentRenderingPipeline",
            steps=[plan_capture_context(), _RenderSnapshotSources()],
        )
