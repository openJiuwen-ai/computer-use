# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize repeated native cards as complete records with shared role headers."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import prefix_signature, revise, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.structure.record_blocks import peer_axis, record_candidate
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Choose record blocks for bounded repeated content that is too complex for columns."""
    if not draft.enabled or draft.atoms is None:
        return draft
    blocks = dict(draft.blocks)
    candidates = {key: record_candidate(draft, block) for key, block in blocks.items()}
    decisions = []
    for parent_key, parent in tuple(blocks.items()):
        if parent.layout != "tree" or parent.kind in ("table", "record", "field"):
            continue
        result = []
        index = 0
        while index < len(parent.children):
            first = candidates.get(parent.children[index])
            if first is None:
                result.append(parent.children[index])
                index += 1
                continue
            members = [first]
            axis = None
            end = index + 1
            while end < len(parent.children):
                candidate = candidates.get(parent.children[end])
                next_axis = peer_axis(members[-1], candidate) if candidate is not None else None
                if (
                    next_axis is None
                    or axis is not None
                    and next_axis != axis
                    or not same_region(
                        draft, (ref for member in (*members, candidate) for ref in block_sources(member.key, blocks))
                    )
                ):
                    break
                axis = next_axis
                members.append(candidate)
                end += 1
            if len(members) < 2 or not any(member.complex for member in members):
                result.extend(parent.children[index:end])
                index = end
                continue
            refs = tuple(member.ref for member in members)
            key = synthetic_key("record-blocks", parent_key, refs)
            signature = prefix_signature(draft, refs[0])
            prefix = refs[0] if all(prefix_signature(draft, ref) == signature for ref in refs) else None
            blocks[key] = InterfaceBlock(
                key,
                "collection",
                None,
                (),
                tuple(member.key for member in members),
                layout="record-blocks",
                origin="visual",
                shared_prefix_ref=prefix,
            )
            for member in members:
                blocks[member.key] = replace(blocks[member.key], kind="record")
            result.append(key)
            decisions.append("complex-records:" + ",".join(refs))
            index = end
        if tuple(result) != parent.children:
            blocks[parent_key] = replace(parent, children=tuple(result))
    return revise(draft, blocks, *decisions)
