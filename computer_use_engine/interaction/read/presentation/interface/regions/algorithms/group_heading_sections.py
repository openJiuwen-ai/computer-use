# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use native headings to expose local sections without inventing actionable regions."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import element_key, revise, same_scope, synthetic_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.headings import HeadingSection, heading_sections
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Group heading-led reading units with their existing containment."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions = []

    def materialize(section: str | HeadingSection, owner: str, siblings: tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(section, str):
            return (section,)
        descendants = []
        pending = list(section.children)
        while pending:
            member = pending.pop()
            if isinstance(member, str):
                descendants.append(member)
            else:
                pending.extend(member.children)
        sources = (source for key in descendants for source in block_sources(key, blocks))
        if not same_scope(draft, (owner, *sources)):
            return tuple(member for member in siblings if member in descendants)
        members = tuple(key for child in section.children for key in materialize(child, owner, siblings))
        key = synthetic_key("section", owner, members)
        blocks[key] = InterfaceBlock(
            key, "region", None, (), members, label_ref=blocks[section.heading].primary_ref, region_role="region"
        )
        return (key,)

    for ref in draft.order:
        node = draft.projected_nodes[ref]
        if node.get("layoutGroups"):
            continue
        if node["role"] not in ("group", "window", "sheet", "scrollArea", "webArea", "document", "tabPanel"):
            continue
        parent = blocks[element_key(ref)]
        children = parent.children
        unit_nodes = {
            key: draft.projected_nodes[blocks[key].primary_ref] if blocks[key].primary_ref is not None else {}
            for key in children
        }
        if draft.evidence[ref].hidden or not any(unit.get("role") == "heading" for unit in unit_nodes.values()):
            continue

        new_children = tuple(
            key for section in heading_sections(children, unit_nodes) for key in materialize(section, ref, children)
        )
        if new_children != parent.children:
            blocks[parent.key] = replace(parent, children=new_children)
            decisions.append("heading-sections:" + ref)
    return revise(draft, blocks, *decisions)
