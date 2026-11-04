# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render prepared snapshot sources before the terminal text-assembly Pass."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayTree
from computer_use.interaction.read.presentation.model import PRESENTATION_FAILURES, PURE
from computer_use.interaction.read.presentation.pipeline import RenderPreparedSnapshotPipeline
from computer_use.interaction.read.presentation.rendering.document.model import (
    DocumentRenderRequest,
    RenderedDocumentInput,
)
from computer_use.interaction.read.presentation.rendering.document.passes.format_document import (
    stage as format_document,
)
from computer_use.interaction.read.presentation.rendering.document.passes.plan_capture_context import (
    stage as plan_capture_context,
)
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Node

_rendered_sources = WeakIdentityMap()

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control, Step


def _snapshot_sources(document):
    """Collect exact source objects, including retained local detail hints."""
    pending, sources = list(reversed(document.parts)), {}
    while pending:
        part = pending.pop()
        if isinstance(part, DisplayTree):
            sources.setdefault(id(part.prepared), part.prepared)
        elif isinstance(part, DisplayText):
            if part.kind == "details_hint":
                sources.setdefault(id(part.values[0]), part.values[0])
            else:
                pending.extend(reversed(part.values))
        elif isinstance(part, (tuple, list)):
            pending.extend(reversed(part))
    return sources


class _RenderSnapshotSources(Node["DocumentRenderRequest", "RenderedDocumentInput"]):
    """Render each retained tree or detail-hint source once within final delivery.

    Attributes:
        presentation: Explicit child flow applying the existing field layout and source
            accounting to each already-compressed snapshot.
    """

    def __init__(self) -> None:
        self.presentation = RenderPreparedSnapshotPipeline()
        self.children = (self.presentation,)
        super().__init__("RenderDocumentSnapshotSources", Contract(DocumentRenderRequest, RenderedDocumentInput, PURE))

    def _run(self, request: DocumentRenderRequest, control: Control) -> RenderedDocumentInput:
        sources = _snapshot_sources(request.document)
        if request.prepared is not None:
            # Capture-time preparation owns the source renders. Delivery may
            # choose a different delta after pending captures are coalesced;
            # it must only select already formatted nodes, never start another
            # complete renderer after the originating operation's deadline.
            original = _snapshot_sources(request.prepared.request.document)
            if set(sources) != set(request.prepared.snapshots) or any(
                original.get(key) is not source for key, source in sources.items()
            ):
                raise ValueError("Prepared delivery does not belong to these exact captured sources")
            return RenderedDocumentInput(request, request.prepared.snapshots)
        sections = {}
        for source in sources.values():
            for root in source.roots:
                for group in root.groups.groups if root.groups is not None else ():
                    signature = (group.capture, group.entries, group.recovery_refs)
                    previous = sections.setdefault(group.section_key, signature)
                    if previous != signature:
                        raise ValueError("Content directory section identifies different captured fields")
        selections = {}
        tree_index = 0
        for part in request.document.parts:
            if not isinstance(part, DisplayTree):
                continue
            delta = (
                request.plan.trees[tree_index]
                if request.plan is not None and tree_index < len(request.plan.trees)
                else None
            )
            eligible = (
                request.plan is not None and request.plan.mode != "full" and not part.details
                and delta is not None
            )
            key = id(part.prepared)
            if not eligible:
                selections[key] = None
            elif key not in selections:
                selections[key] = set(delta.changes).union(delta.context)
            elif selections[key] is not None:
                selections[key].update(delta.changes)
                selections[key].update(delta.context)
            tree_index += 1
        snapshots = {}
        for key, source in sources.items():
            if control.remaining is not None:
                control.remaining()
            if key in selections and selections[key] is not None:
                from .incremental import incremental_snapshot

                try:
                    rendered = incremental_snapshot(source, frozenset(selections[key]), checkpoint=control.remaining)
                except PRESENTATION_FAILURES:
                    # This is pure presentation over the same capture. A local
                    # selection failure uses its complete terminal renderer;
                    # it never reacquires facts or replays an input operation.
                    rendered = None
                if rendered is not None:
                    snapshots[key] = rendered
                    continue
            rendered = _rendered_sources.get(source)
            if rendered is None:
                rendered = self.presentation.run(source, control)
                if control.remaining is not None:
                    control.remaining()
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
