# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable session numbers, allocated in the existing interleaved display order.

Each part is previewed then assigned before the next preview. A later display
failure must not undo alias touches already made for earlier parts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.model import (
    PURE,
    NumberingRequest,
    NumberingRun,
    _PartPreview,
)
from computer_use.interaction.read.numbering.passes.assign_preview_references import (
    stage as assign_preview_references,
)
from computer_use.interaction.read.numbering.passes.assign_unrendered_references import (
    stage as assign_unrendered_references,
)
from computer_use.interaction.read.numbering.passes.bind_numbering_session import (
    stage as bind_numbering_session,
)
from computer_use.interaction.read.numbering.passes.no_snapshot_numbers import (
    stage as no_snapshot_numbers,
)
from computer_use.interaction.read.numbering.passes.rewrite_structured_refs import (
    stage as rewrite_structured_refs,
)
from computer_use.interaction.read.numbering.passes.snapshots_are_empty import (
    stage as snapshots_are_empty,
)
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.pipeline import Contract, Pipeline, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from typing import Iterable

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.records import ReferenceAllocator
    from computer_use.pipeline.core import Control, Step


class _PreviewSnapshot(Node["_PartPreview", "_PartPreview"]):
    """Prepare a snapshot projection to determine visible reference order.

    Compress the current snapshot without text rendering and attach the projection to the
    preview input. Number allocation follows its visible pre-order references.

    Attributes:
        presentation: Structured preparation flow used to obtain visible reference order.
    """

    def __init__(self) -> None:
        self.presentation = PrepareSnapshotPipeline()
        self.children = (self.presentation,)
        super().__init__("PreviewSnapshot", Contract(_PartPreview, _PartPreview, PURE))

    def _run(self, value: _PartPreview, control: Control) -> _PartPreview:
        from dataclasses import replace

        prepared = self.presentation.run(SnapshotRenderRequest(value.snapshot, compact=True), control)
        return replace(value, prepared=prepared)

    async def _arun(self, value: _PartPreview, control: Control) -> _PartPreview:
        return self._run(value, control)


class _PreviewAndAssign(Node["NumberingRun", "NumberingRun"]):
    """Allocate visible references for each snapshot in order.

    Run structured preparation and alias assignment for each supplied snapshot. Append each
    native-to-display map to the numbering run in matching snapshot order.

    Attributes:
        part: Per-snapshot preview and alias-assignment flow.
    """

    def __init__(self) -> None:
        self.part = Sequence(name="PreviewAndAssignPart", steps=[_PreviewSnapshot(), assign_preview_references()])
        self.children = (self.part,)
        super().__init__("PreviewAndAssignParts", Contract(NumberingRun, NumberingRun, self.part.contract.effects))

    def _run(self, value: NumberingRun, control: Control) -> NumberingRun:
        return NumberingRun(
            value.snapshots,
            value.references,
            tuple(self.part.run(_PartPreview(snapshot, value.references), control) for snapshot in value.snapshots),
        )

    async def _arun(self, value: NumberingRun, control: Control) -> NumberingRun:
        return self._run(value, control)


class NumberSnapshotsPipeline(Pipeline["NumberingRequest", "tuple[SemanticSnapshot, ...]"]):
    """Assign shared display aliases to a sequence of native snapshots.

    Bind the snapshots to their driver-session allocator and allocate visible references
    before remaining source objects. Rebuild semantic snapshots with the resulting aliases,
    or return an empty tuple for an empty request.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[NumberingRequest, tuple[SemanticSnapshot, ...]]:
        return Select(
            name="NumberSnapshotsPipeline",
            selector=snapshots_are_empty(),
            branches={
                True: no_snapshot_numbers(),
                False: Sequence(
                    name="NumberedSnapshotParts",
                    steps=[
                        bind_numbering_session(),
                        _PreviewAndAssign(),
                        assign_unrendered_references(),
                        rewrite_structured_refs(),
                    ],
                ),
            },
        )


def number_snapshots(
    snapshots: Iterable[SemanticSnapshot],
    references: ReferenceAllocator | None = None,
    *,
    _control: Control | None = None,
) -> tuple[SemanticSnapshot, ...]:
    """Number one result's parts without merging their native binding scopes."""
    return NumberSnapshotsPipeline().run(NumberingRequest(tuple(snapshots), references), _control)
