# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.protocol.primitives import frozen

if TYPE_CHECKING:

    from computer_use.protocol.records import Record


def tree_collection_issues(snapshot):
    """Return explicit whole-tree collection gaps from this exact native capture.

    Optional field support and unrelated referenced parent objects are not tree
    collection failures. A returned root also cannot turn a native nodes/roots
    partial or timeout record into a complete observation.
    """
    return tuple(snapshot.capture.collection_issues())


def document_coverage(document: DisplayDocument) -> Mapping[str, Any]:
    """Retain collection evidence from the same captured facts as the delivered tree.

    Complete child collections are deliberately not inferred from formatted
    child counts: normalization omits those counts for complete collections.
    Related roots and partial/unread children remain absence, not proven removal.
    """
    parents, complete = {}, set()
    for tree in document.parts:
        if not isinstance(tree, DisplayTree):
            continue
        snapshot = tree.prepared.snapshot
        native_parents, native_complete = snapshot.numbered.delivery_coverage()
        parents.update(native_parents)
        complete.update(native_complete)
    return frozen({"parents": frozen(parents), "complete": tuple(sorted(complete))})


def native_coverage(coverage):
    """Pass the exact delivered coverage envelope to a native comparison."""
    if coverage is None:
        return None
    return dict(coverage["parents"]), coverage["complete"]


def has_unconfirmed_missing(previous: Record, current: Record, ignored: set[str]) -> bool:
    """Check pending coverage using the same proof as tree comparison."""
    if not previous["refs"]:
        return False
    from computer_use.interaction.read.accessibility._native import require_read_engine

    return require_read_engine().has_unconfirmed_read_missing(
        tuple(previous["refs"]), tuple(current["refs"]), tuple(ignored),
        native_coverage(previous.get("coverage")), native_coverage(current.get("coverage")),
    )


if TYPE_CHECKING:
    from typing import Any, Mapping
