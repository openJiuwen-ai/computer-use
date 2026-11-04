# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share exact prefixes across adjacent planned rows without removing row boundaries."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import (
    prefix_signature,
    revise,
    same_scope,
    synthetic_key,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Wrap consecutive homogeneous inline rows in one source-backed prefix scope."""
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    if not draft.enabled:
        return draft
    for parent_key, parent in tuple(blocks.items()):
        if parent.layout != "tree":
            continue
        children = parent.children
        result = []
        index = 0
        while index < len(children):
            first = blocks[children[index]]
            if first.layout != "inline" or first.shared_prefix_ref is None:
                result.append(children[index])
                index += 1
                continue
            signature = prefix_signature(draft, first.shared_prefix_ref)
            end = index + 1
            while end < len(children):
                peer = blocks[children[end]]
                if (
                    peer.layout != "inline"
                    or peer.shared_prefix_ref is None
                    or not same_scope(draft, (first.shared_prefix_ref, peer.shared_prefix_ref))
                    or not same_region(draft, (first.shared_prefix_ref, peer.shared_prefix_ref))
                    or prefix_signature(draft, peer.shared_prefix_ref) != signature
                ):
                    break
                end += 1
            if end == index + 1:
                result.append(children[index])
            else:
                key = synthetic_key("inline-rows", parent_key, children[index:end])
                blocks[key] = InterfaceBlock(
                    key,
                    "collection",
                    None,
                    (),
                    children[index:end],
                    layout="rows",
                    shared_prefix_ref=first.shared_prefix_ref,
                    origin=first.origin,
                )
                result.append(key)
                decisions.append("shared-row-header:" + key)
            index = end
        if tuple(result) != children:
            blocks[parent_key] = replace(parent, children=tuple(result))
    return revise(draft, blocks, *decisions)
