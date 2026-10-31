# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reuse established native row and column proofs before choosing any layout."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.collections.model import CollectionEvidence, CollectionRequest
from computer_use.interaction.read.presentation.rendering.layout.row_collections import capture_row_collections
from computer_use.interaction.read.presentation.rendering.layout.table_containment import capture_table_grids
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: CollectionRequest, control: Control) -> CollectionEvidence:
    """Capture complete native collection edges using the original source aliases."""
    return CollectionEvidence(
        request,
        capture_row_collections(request.native, request.refs),
        capture_table_grids(request.native, request.refs, source_scopes=request.interface.source.scopes),
    )


stage = Pass.define(
    name="capture_native_collections",
    function=execute,
    contract=Contract(
        CollectionRequest,
        CollectionEvidence,
        {Effect.PURE},
        requires=("captured native collection facts and original aliases",),
        writes=("immutable native row and column proofs",),
    ),
)
