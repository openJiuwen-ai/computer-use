# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose compact runs of homogeneous independent leaf objects before text rendering."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import (
    CONTROL_ROLES,
    prefix_signature,
    revise,
    same_scope,
    single_line,
    synthetic_key,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.structure.geometry import inline_rows
from computer_use.interaction.read.presentation.interface.geometric.boundaries import same_region


_INLINE_ROLES = CONTROL_ROLES | frozenset(("row", "menuBarItem", "menuItem", "staticText"))


def _incomplete(draft: InterfaceDraft, ref: str) -> bool:
    """Permit a declared unopened menu item while retaining its child-coverage summary."""
    return draft.evidence[ref].incomplete and not (
        draft.atoms is not None and draft.atoms.atoms[ref].coverage.unexpanded_menu
    )


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Share one prefix across short consecutive members while retaining all references."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    for parent_key, parent in tuple(blocks.items()):
        if parent.layout != "tree" or parent.kind in ("record", "table"):
            continue
        children = parent.children
        result = []
        index = 0
        while index < len(children):
            first = blocks[children[index]]
            ref = first.primary_ref
            if (
                ref is None
                or first.children
                or first.layout != "tree"
                or draft.projected_nodes[ref]["role"] not in _INLINE_ROLES
                or _incomplete(draft, ref)
                or draft.evidence[ref].hidden
                or not single_line(draft.projected_nodes[ref])
            ):
                result.append(children[index])
                index += 1
                continue
            signature = prefix_signature(draft, ref)
            end = index + 1
            refs = [ref]
            characters = sum(
                len(str(draft.projected_nodes[ref].get(field, ""))) for field in ("name", "value", "description")
            )
            while end < len(children):
                member = blocks[children[end]]
                target = member.primary_ref
                if target is None or member.children or member.layout != "tree":
                    break
                node = draft.projected_nodes[target]
                cost = sum(len(str(node.get(field, ""))) for field in ("name", "value", "description"))
                if (
                    prefix_signature(draft, target) != signature
                    or _incomplete(draft, target)
                    or draft.evidence[target].hidden
                    or not single_line(node)
                    or not same_scope(draft, (*refs, target))
                    or not same_region(draft, (*refs, target))
                    or characters + cost > 240
                ):
                    break
                refs.append(target)
                characters += cost
                end += 1
            rows = inline_rows(refs, draft.source.geometry)
            row_index = index
            for row in rows:
                member_keys = children[row_index : row_index + len(row)]
                # Three members amortize the shared header; proven row breaks stay visible.
                if len(row) >= 3:
                    key = synthetic_key("inline", parent_key, row)
                    blocks[key] = InterfaceBlock(
                        key,
                        "collection",
                        None,
                        (),
                        member_keys,
                        layout="inline",
                        shared_prefix_ref=row[0],
                        origin="visual" if len(rows) > 1 else "native",
                    )
                    result.append(key)
                    decisions.append("inline-members:" + ",".join(row))
                else:
                    result.extend(member_keys)
                row_index += len(row)
            index = end
        if tuple(result) != children:
            blocks[parent_key] = replace(parent, children=tuple(result))
    return revise(draft, blocks, *decisions)
