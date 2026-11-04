# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Represent repeated empty controls by a shared prefix and exact reference set."""

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


def _empty_atom(draft: InterfaceDraft, key: str) -> str | None:
    """Find a complete leaf whose complete public meaning is its identity and prefix."""
    block = draft.blocks[key]
    ref = block.primary_ref
    if (
        ref is None
        or block.children
        or block.layout != "tree"
        or block.kind in ("window", "region", "field", "record", "table")
        or draft.evidence[ref].incomplete
        or draft.evidence[ref].hidden
    ):
        return None
    atom = draft.atoms.atoms[ref]
    if (
        atom.slots
        or atom.actions
        or atom.actions_unavailable
        or atom.declarations
        or atom.coverage.partial
        or atom.coverage.unexpanded_menu
    ):
        return None
    return ref


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Compact consecutive, identical empty siblings without implying a visual row."""
    if not draft.enabled or draft.atoms is None:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    for parent_key, parent in tuple(blocks.items()):
        if parent.layout != "tree" or parent.kind in ("table", "record", "field"):
            continue
        children = parent.children
        result = []
        index = 0
        while index < len(children):
            ref = _empty_atom(draft, children[index])
            if ref is None:
                result.append(children[index])
                index += 1
                continue
            signature = prefix_signature(draft, ref)
            native_parent = draft.source.parents.get(ref)
            end, refs = index + 1, [ref]
            while end < len(children):
                candidate = _empty_atom(draft, children[end])
                if candidate is None:
                    break
                if (
                    prefix_signature(draft, candidate) != signature
                    or draft.source.parents.get(candidate) != native_parent
                    or not same_scope(draft, (*refs, candidate))
                    or not same_region(draft, (*refs, candidate))
                ):
                    break
                refs.append(candidate)
                end += 1
            if len(refs) < 3:
                result.extend(children[index:end])
            else:
                key = synthetic_key("compact-list", parent_key, refs)
                blocks[key] = InterfaceBlock(
                    key, "collection", None, (), children[index:end], layout="compact-list", shared_prefix_ref=ref
                )
                result.append(key)
                decisions.append("empty-atom-list:" + ",".join(refs))
            index = end
        if tuple(result) != children:
            blocks[parent_key] = replace(parent, children=tuple(result))
    return revise(draft, blocks, *decisions)
