# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Index immutable sources and native scope boundaries for final organization."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.evidence.algorithms.index_sources import (
    apply as index_sources,
)
from computer_use.interaction.read.presentation.interface.evidence.algorithms.derive_boundaries import (
    apply as derive_boundaries,
)
from computer_use.interaction.read.presentation.interface.regions.algorithms.seed_regions import apply as seed_regions
from dataclasses import replace
from computer_use.interaction.read.presentation.interface.geometric.source import PreparedGeometricSources
from computer_use.interaction.read.presentation.interface.model import InterfaceRequest, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(request: InterfaceRequest, control: Control) -> InterfaceDraft:
    """Index immutable sources and native scope boundaries for final organization."""
    draft = index_sources(request)
    draft = derive_boundaries(draft)
    draft = seed_regions(draft)
    draft = replace(draft, geometric_sources=PreparedGeometricSources.build(request, request.captured))
    return draft


stage = Pass.define(
    name="GeometricScopePass",
    function=execute,
    contract=Contract(
        InterfaceRequest,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable captured facts and explicit source membership",),
        writes=("local geometric scope plan",),
    ),
)
