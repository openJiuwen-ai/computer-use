# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share native associations and geometric alternatives before choosing regions."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.relation_evidence import capture_geometric_relations
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Retain one scoped relationship graph for downstream layout decisions."""
    return capture_geometric_relations(draft)


stage = Pass.define(
    name="GeometricRelationPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("whole-unit alignment and explicit native associations",),
        writes=("immutable local relationship candidates and constraints",),
    ),
)
