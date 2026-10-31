# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish a detached structured interface without generating display text."""

from __future__ import annotations

from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.model import InterfaceDocument, InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDocument:
    """Freeze the validated block graph and its one-to-one real-source location index."""
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
        relations=draft.relations,
    )


stage = Pass.define(
    name="freeze_interface_document",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDocument,
        {Effect.PURE},
        requires=("validated block and reference plan",),
        writes=("frozen interface document",),
    ),
)
