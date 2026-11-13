# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan incremental delivery from retained accessibility comparison data."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.accessibility._native import require_read_engine
from computer_use.interaction.read.delivery.coverage import native_coverage
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument, native_current_tree
from computer_use.interaction.read.presentation.delta import DeltaPlan, RustDeltaPlan
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree

if TYPE_CHECKING:
    from collections.abc import Mapping

    from computer_use.protocol.records import Record


def plan_document_diff(
    before: ComparisonDocument | DisplayDocument | None,
    after: DisplayDocument | None,
    previous_refs: Mapping[str, object],
    current_refs: Mapping[str, object],
    not_observed: set[str] | frozenset[str] = frozenset(),
    *,
    coverage: tuple[Record | None, Record | None] | None = None,
) -> DeltaPlan | RustDeltaPlan:
    """Choose complete or incremental delivery using only structured observation data."""
    if isinstance(before, DisplayDocument):
        before = ComparisonDocument.capture(before)
    matches_before = not isinstance(before, ComparisonDocument) or not isinstance(after, DisplayDocument)
    if matches_before:
        return DeltaPlan(reason="no-structured-baseline")
    if not before.trees:
        return DeltaPlan()
    current = tuple(native_current_tree(part) for part in after.parts if isinstance(part, DisplayTree))
    handle = require_read_engine().plan_read_document_diff(
        tuple(tree.handle for tree in before.trees),
        current,
        dict(previous_refs),
        dict(current_refs),
        tuple(not_observed),
        coverage=tuple(native_coverage(item) for item in coverage) if coverage is not None else None,
    )
    return RustDeltaPlan(handle)
