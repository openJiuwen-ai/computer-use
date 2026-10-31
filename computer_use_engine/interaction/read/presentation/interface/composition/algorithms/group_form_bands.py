# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize repeated form rows before choosing any caption compression."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.composition.forms import form_bands
from computer_use.interaction.read.presentation.interface.helpers import revise, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Keep complete original members in labelled visual fields with no synthetic target."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    for parent_key, parent in draft.blocks.items():
        bands = form_bands(parent, draft)
        if not bands:
            continue
        starts = {band.children[0]: band for band in bands}
        children = []
        index = 0
        while index < len(parent.children):
            child = parent.children[index]
            band = starts.get(child)
            if band is None:
                children.append(child)
                index += 1
                continue
            key = synthetic_key("form-band", parent_key, band.children)
            blocks[key] = InterfaceBlock(
                key, "field", None, (), band.children, layout="fields", origin="visual", label_ref=band.label
            )
            children.append(key)
            index += len(band.children)
            decisions.append("form-band:" + band.label)
        blocks[parent_key] = replace(parent, children=tuple(children))
    return revise(draft, blocks, *decisions)
