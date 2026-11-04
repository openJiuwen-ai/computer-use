# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use native headings to expose local sections without inventing actionable regions."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import element_key, revise, synthetic_key, same_scope
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.headings import HeadingSection, heading_sections


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Group heading-led sibling spans while keeping headings and content independently bound."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    for ref in draft.order:
        node = draft.projected_nodes[ref]
        if node.get("layoutGroups"):
            continue
        if node["role"] not in ("group", "window", "sheet", "scrollArea", "webArea", "document", "tabPanel"):
            continue
        children = tuple(node.get("children", ()))
        if not any(draft.projected_nodes[child]["role"] == "heading" for child in children):
            continue
        if draft.evidence[ref].hidden:
            continue

        def materialize(section: str | HeadingSection) -> tuple[str, ...]:
            if isinstance(section, str):
                return (element_key(section),)
            descendants = []
            pending = list(section.children)
            while pending:
                member = pending.pop()
                if isinstance(member, str):
                    descendants.append(member)
                else:
                    pending.extend(member.children)
            if not same_scope(draft, (ref, *descendants)):
                return tuple(element_key(member) for member in children if member in descendants)
            members = tuple(key for child in section.children for key in materialize(child))
            key = synthetic_key("section", ref, members)
            blocks[key] = InterfaceBlock(
                key, "region", None, (), members, label_ref=section.heading, region_role="region"
            )
            return (key,)

        new_children = tuple(
            key for section in heading_sections(children, draft.projected_nodes) for key in materialize(section)
        )
        if new_children != blocks[element_key(ref)].children:
            blocks[element_key(ref)] = replace(blocks[element_key(ref)], children=new_children)
            decisions.append("heading-sections:" + ref)
    return revise(draft, blocks, *decisions)
