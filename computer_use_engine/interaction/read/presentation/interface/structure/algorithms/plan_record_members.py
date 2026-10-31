# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan short control runs and explicit wrapper paths inside complete record blocks."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import (
    prefix_signature,
    revise,
    same_scope,
    synthetic_key,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.structure.record_blocks import (
    confirmed_children,
    known_control,
)
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region


def _inline_control(draft: InterfaceDraft, block: InterfaceBlock) -> bool:
    """Keep each short independent control intact while sharing only its typed prefix."""
    ref = block.primary_ref
    if ref is None or block.children or block.layout != "tree" or not known_control(draft, ref):
        return False
    atom = draft.atoms.atoms[ref]
    return (
        not atom.coverage.partial
        and not atom.declarations
        and all(
            slot.placement != "block"
            and slot.preview is None
            and (
                not isinstance(slot.value, str)
                or len(slot.value) <= 100
                and not any(character in slot.value for character in "\r\n\t")
            )
            for slot in atom.slots
        )
    )


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Expose record fields as original ancestor paths and keep nearby actions compact."""
    if not draft.enabled or draft.atoms is None:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    pending = [child for block in blocks.values() if block.layout == "record-blocks" for child in block.children]
    ordered = []
    while pending:
        key = pending.pop()
        ordered.append(key)
        pending.extend(reversed(blocks[key].children))
    for key in reversed(ordered):
        block = blocks[key]
        if block.layout != "tree" or block.kind in ("window", "field", "table", "region"):
            continue
        children = []
        index = 0
        while index < len(block.children):
            first = blocks[block.children[index]]
            if not _inline_control(draft, first):
                children.append(first.key)
                index += 1
                continue
            members = [first]
            signature = prefix_signature(draft, first.primary_ref)
            end = index + 1
            while end < len(block.children):
                other = blocks[block.children[end]]
                if (
                    not _inline_control(draft, other)
                    or prefix_signature(draft, other.primary_ref) != signature
                    or not same_region(draft, (first.primary_ref, other.primary_ref))
                    or not same_scope(draft, (first.primary_ref, other.primary_ref))
                ):
                    break
                members.append(other)
                end += 1
            if len(members) < 2:
                children.append(first.key)
            else:
                refs = tuple(member.primary_ref for member in members)
                group_key = synthetic_key("record-controls", key, refs)
                blocks[group_key] = InterfaceBlock(
                    group_key,
                    "collection",
                    None,
                    (),
                    tuple(member.key for member in members),
                    layout="inline",
                    shared_prefix_ref=refs[0],
                )
                children.append(group_key)
                decisions.append("record-controls:" + ",".join(refs))
            index = end
        block = replace(block, children=tuple(children))
        blocks[key] = block
        ref = block.primary_ref
        if (
            ref is None
            or block.kind == "record"
            or draft.projected_nodes[ref]["role"] != "group"
            or len(block.children) != 1
        ):
            continue
        child = blocks[block.children[0]]
        target = child.primary_ref
        if (
            target is None
            or child.kind in ("window", "region", "field", "record", "table")
            or child.layout not in ("tree", "record-path")
            or draft.projected_nodes[ref]["children"] != (target,)
            or draft.source.parents.get(target) != ref
            or not confirmed_children(draft, ref, (target,))
            or not same_scope(draft, (ref, target))
        ):
            continue
        atom = draft.atoms.atoms[ref]
        if (
            atom.coverage.partial
            or atom.declarations
            or any(
                slot.placement == "block"
                or isinstance(slot.value, str)
                and (len(slot.value) > 100 or any(character in slot.value for character in "\n\r\t"))
                for slot in atom.slots
            )
        ):
            continue
        blocks[key] = replace(block, layout="record-path")
        decisions.append("record-path:" + ref + ">" + target)
    return revise(draft, blocks, *decisions)
