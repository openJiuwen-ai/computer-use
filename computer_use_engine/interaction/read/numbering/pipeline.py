# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable session numbers, allocated in the existing interleaved display order.

Each part is previewed then assigned before the next preview. A later display
failure must not undo alias touches already made for earlier parts.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
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
from computer_use.pipeline import Contract, Effect, Pass, Pipeline, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence as EventSequence

    from computer_use.interaction.read.accessibility.snapshot import ProjectionEvent, ReadSnapshot
    from computer_use.interaction.read.presentation.records import ReferenceAllocator
    from computer_use.pipeline.core import Control, Step


_LOCAL_COMPRESSION_RULES = frozenset({
    "promote_description", "merge_editors", "merge_control_labels", "merge_repeated_text",
    "merge_region_labels", "flatten_redundant_hierarchy", "prune_layout", "prune_empty_rows",
    "collapse_transparent_groups",
})
_SPATIAL_RULES = frozenset(
    {"associate_spatial_labels", "group_spatial_rows", "describe_spatial_regions", "mark_visual_chains"}
)
_RESOURCE_STAGES = frozenset({"PresentationPipeline", "CompressionPipeline", "CompressionIRLifetime"})
_PURE_EFFECTS = frozenset(PURE)
_RESOURCE_EFFECTS = _PURE_EFFECTS | {Effect.RESOURCE}
_PROJECTION_ERRORS = {"KeyError": KeyError, "ValueError": ValueError}


def _emit_projection_trace(control: Control, events: EventSequence[ProjectionEvent]) -> None:
    """Attach each actual native invocation to its original host diagnostic path."""
    presentation = control.child("PresentationPipeline")
    semantic = presentation.child("SemanticPipeline")
    compression = presentation.child("CompressionPipeline")
    lifetime = compression.child("CompressionIRLifetime")
    export = lifetime.child("CompressAndExport")
    traversal = export.child("CompressionTraversal")
    spatial = export.child("SpatialOrganizationPipeline")
    stages = {
        "PresentationPipeline": presentation,
        "SemanticPipeline": semantic,
        "compose_display_semantics": semantic.child("compose_display_semantics"),
        "build_projection_input": semantic.child("build_projection_input"),
        "collect_geometry": semantic.child("collect_geometry"),
        "CompressionPipeline": compression,
        "build_presentation_ir": compression.child("build_presentation_ir"),
        "CompressionIRLifetime": lifetime,
        "CompressAndExport": export,
        "CompressionTraversal": traversal,
        "SpatialOrganizationPipeline": spatial,
    }
    timestamp, previous_ms = None, None
    for name, phase, error, timestamp_ms in events:
        if timestamp_ms != previous_ms:
            previous_ms = timestamp_ms
            timestamp = None
            if timestamp_ms is not None:
                seconds, milliseconds = divmod(timestamp_ms, 1000)
                time = datetime.fromtimestamp(seconds, UTC).replace(microsecond=milliseconds * 1000)
                timestamp = time.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        stage = stages.get(name)
        if stage is None:
            parent = traversal if name in _LOCAL_COMPRESSION_RULES else spatial if name in _SPATIAL_RULES else export
            stage = stages[name] = parent.child(name)
        effects = _RESOURCE_EFFECTS if name in _RESOURCE_STAGES else _PURE_EFFECTS
        stage.emit(phase, effects, _PROJECTION_ERRORS[error]() if error is not None else None, timestamp=timestamp)


def _preview_native_roots(value: _PartPreview, control: Control) -> _PartPreview:
    """Collect the visible order from a genuine native root preparation."""
    observer = partial(_emit_projection_trace, control) if control.diagnostic is not None else None
    return replace(value, visible_tokens=value.snapshot.preview_tokens(control.remaining, observer))


class _PreviewSnapshot(Node["_PartPreview", "_PartPreview"]):
    """Prepare a snapshot projection to determine visible reference order.

    Compress the current snapshot without text rendering and attach the projection to the
    preview input. Number allocation follows its visible pre-order references.

    Attributes:
        presentation: Structured preparation flow used to obtain visible reference order.
    """

    def __init__(self) -> None:
        self.presentation = Pass(
            name="PrepareSnapshotRoots",
            function=_preview_native_roots,
            contract=Contract(_PartPreview, _PartPreview, PURE),
        )
        self.children = (self.presentation,)
        super().__init__("PreviewSnapshot", Contract(_PartPreview, _PartPreview, PURE))

    def _run(self, value: _PartPreview, control: Control) -> _PartPreview:
        return self.presentation.run(value, control)

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


class NumberSnapshotsPipeline(Pipeline["NumberingRequest", "tuple[ReadSnapshot, ...]"]):
    """Assign shared display aliases to a sequence of native snapshots.

    Bind the snapshots to their driver-session allocator and allocate visible references
    before remaining source objects. Rebuild semantic snapshots with the resulting aliases,
    or return an empty tuple for an empty request.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[NumberingRequest, tuple[ReadSnapshot, ...]]:
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
    snapshots: Iterable[ReadSnapshot],
    references: ReferenceAllocator | None = None,
    *,
    _control: Control | None = None,
) -> tuple[ReadSnapshot, ...]:
    """Number one result's parts without merging their native binding scopes."""
    return NumberSnapshotsPipeline().run(NumberingRequest(tuple(snapshots), references), _control)
