# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Classify real controls and content without rewriting their capabilities or values."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import (
    CONTROL_ROLES,
    FIELD_ROLES,
    TEXT_ROLES,
    element_key,
    revise,
)
from computer_use.interaction.read.presentation.interface.model import BlockKind, InterfaceDraft


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Give projected input objects, controls and passive content their own block kinds."""
    blocks = dict(draft.blocks)
    for ref, node in draft.projected_nodes.items():
        role = node["role"]
        kind: BlockKind | None = (
            "control"
            if role in FIELD_ROLES | CONTROL_ROLES
            else "record"
            if role == "row"
            else "content"
            if role in TEXT_ROLES
            else None
        )
        if kind is not None:
            blocks[element_key(ref)] = replace(blocks[element_key(ref)], kind=kind)
    return revise(draft, blocks)
