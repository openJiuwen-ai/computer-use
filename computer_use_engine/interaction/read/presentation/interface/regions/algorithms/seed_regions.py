# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build initial blocks from semantic sources, preserving their original containment."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.helpers import COLLECTION_ROLES, REGION_ROLES, element_key
from computer_use.interaction.read.presentation.interface.model import BlockKind, InterfaceBlock, InterfaceDraft


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Seed one exact-source block per projected object before making synthetic groups."""
    blocks = {}
    for ref, node in draft.projected_nodes.items():
        role = node["role"]
        region_role = (
            "toolbar"
            if role == "toolbar"
            else "navigation"
            if role == "navigation" or node.get("landmarkType") == "navigation"
            else None
        )
        kind: BlockKind = (
            "window"
            if role in ("window", "sheet")
            else "table"
            if role == "table"
            else "region"
            if region_role
            else "collection"
            if role in COLLECTION_ROLES
            else "region"
            if role in REGION_ROLES or node.get("spatialPosition")
            else "tree"
        )
        key = element_key(ref)
        position = node.get("spatialPosition")
        blocks[key] = InterfaceBlock(
            key,
            kind,
            ref,
            (ref,),
            tuple(element_key(child) for child in node.get("children", ())),
            position=position if position in ("left", "middle", "right", "top", "bottom") else None,
            region_role=region_role or ("region" if kind == "region" else None),
        )
    return replace(draft, roots=tuple(element_key(ref) for ref in draft.root_refs), blocks=MappingProxyType(blocks))
