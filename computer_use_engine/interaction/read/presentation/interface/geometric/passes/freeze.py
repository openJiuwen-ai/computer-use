# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Freeze the verified layout and its unchanged original-target inventory."""

from __future__ import annotations

from types import MappingProxyType
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument, InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.inspection import layout_issues, validate_sources
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDocument:
    """Publish the final layout after checking any gate-selected local recovery."""
    validate_sources(draft)
    if layout_issues(draft):
        raise ValueError("Geometric recovery did not preserve a valid local layout")
    locations = {ref: key for key, block in draft.blocks.items() for ref in block.source_refs}
    return InterfaceDocument(
        draft.source,
        draft.projected_nodes,
        draft.projected_parents,
        draft.provenance,
        draft.roots,
        MappingProxyType(dict(draft.blocks)),
        MappingProxyType(locations),
        draft.order,
        draft.root_refs,
        draft.folded_refs,
        draft.decisions,
        format_version="geometric-2",
        relations=draft.relations,
        geometric_sources=draft.geometric_sources,
        geometric_patterns=draft.geometric_patterns,
    )


stage = Pass.define(
    name="GeometricFreezePass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDocument,
        {Effect.PURE},
        requires=("verified source conservation and final layout",),
        writes=("immutable interface document",),
    ),
)
