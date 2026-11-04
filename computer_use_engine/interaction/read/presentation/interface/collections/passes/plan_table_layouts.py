# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose complete logical row layouts from original fields and native membership."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.collections.model import (
    CollectionDocument,
    CollectionEvidence,
)
from computer_use.interaction.read.presentation.interface.collections.planning import plan_collections
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(evidence: CollectionEvidence, control: Control) -> CollectionDocument:
    """Retain each cell extent and action owner while selecting table or row-block layout."""
    return plan_collections(evidence)


stage = Pass.define(
    name="plan_table_layouts",
    function=execute,
    contract=Contract(
        CollectionEvidence,
        CollectionDocument,
        {Effect.PURE},
        requires=("native logical structure and immutable display atoms",),
        writes=("frozen logical collection layouts and local rejection reasons",),
    ),
)
