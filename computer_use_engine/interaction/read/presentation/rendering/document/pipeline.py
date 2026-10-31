# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render prepared snapshot sources before the terminal text-assembly Pass."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayTree
from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.pipeline import RenderPreparedSnapshotPipeline
from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
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

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control, Step


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
        pending: list[object] = list(reversed(request.document.parts))
        sources: dict[int, PreparedSnapshot] = {}
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
        sections = {}
        for source in sources.values():
            for root in source.roots:
                for group in root.groups.groups if root.groups is not None else ():
                    signature = (group.capture, group.entries, group.recovery_refs)
                    previous = sections.setdefault(group.section_key, signature)
                    if previous != signature:
                        raise ValueError("Content directory section identifies different captured fields")
        snapshots = {key: self.presentation.run(source, control) for key, source in sources.items()}
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
