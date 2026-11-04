# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Arrange bounded local regions while retaining unknown-position sources."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.algorithms.group_heading_sections import (
    apply as group_heading_sections,
)
from computer_use.interaction.read.presentation.interface.geometric.algorithms.regions import (
    canonicalize_scopes,
    arrange_regions,
)
from computer_use.interaction.read.presentation.interface.geometric.candidates import collect_scope_plans
from computer_use.interaction.read.presentation.interface.composition.algorithms.group_form_bands import (
    apply as group_form_bands,
)
from computer_use.interaction.read.presentation.interface.composition.algorithms.associate_field_labels import (
    apply as associate_field_labels,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Arrange independent measured segments, form bands and neutral regions."""
    draft = group_heading_sections(draft)
    draft = canonicalize_scopes(draft)
    draft = group_form_bands(draft)
    draft = associate_field_labels(draft)
    draft = arrange_regions(draft)
    return collect_scope_plans(draft)


stage = Pass.define(
    name="GeometricRegionPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable captured facts and explicit source membership",),
        writes=("local geometric region plan",),
    ),
)
