# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep logical collection indices visible and provider-local indices in captured details."""

from __future__ import annotations

from computer_use.interaction.read.presentation.ir import ProjectionResult
from computer_use.interaction.read.presentation.spatial.policy import LOGICAL_COLLECTIONS


def index_has_collection_context(ref: str, projection: ProjectionResult) -> bool:
    """Check original ancestry for a sequence, table coordinate or disclosure model.

    Args:
        ref: Original source reference owning the index.
        projection: Immutable original hierarchy retained before visual grouping.

    Returns:
        Whether the index helps locate a member in a logical collection. An
        unresolved ancestry keeps the index visible for subsequent inspection.
    """
    current: str | None = ref
    seen: set[str] = set()
    while current is not None:
        node = projection.source.native_nodes.get(current)
        if current in seen or node is None:
            return True
        seen.add(current)
        if node["role"] in LOGICAL_COLLECTIONS or any(
            key in node
            for key in ("rowRange", "columnRange", "rowCount", "columnCount", "disclosureLevel", "disclosedBy")
        ):
            return True
        current = projection.source.parents.get(current)
    return False
