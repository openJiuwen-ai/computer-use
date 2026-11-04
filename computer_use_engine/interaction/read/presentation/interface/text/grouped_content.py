# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize selected content overviews while retaining each original atomic record."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.interface.disclosure.groups.formatting import format_group_overview
from computer_use.interaction.read.presentation.interface.disclosure.groups.model import BackgroundOverview

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceBlock
    from computer_use.interaction.read.presentation.interface.text.formatting import InterfaceWriter


def write_grouped_children(
    writer: InterfaceWriter, block: InterfaceBlock, depth: int, context: tuple[tuple[str, str], ...]
) -> None:
    """Render the exact child runs chosen by the disclosure pipeline.

    Nonselected children use their existing layouts. Hidden children retain full
    independent records for incremental delivery and same-observation details.
    """
    plan = writer.request.groups
    groups = {group.child_keys[0]: group for group in plan.groups if group.parent_key == block.key}
    indent = "\t" * depth
    if block.primary_ref is not None:
        writer.write_atom(block.primary_ref, depth, context)
        depth += 1
    elif block.kind == "region":
        heading = indent + {"navigation": "Navigation", "toolbar": "Toolbar"}.get(block.region_role, "Region")
        heading += (" [" + block.position + "]" if block.position else "") + ":"
        writer.lines.append(heading)
        context = (*context, (block.key, heading))
        depth += 1

    # Canonical hierarchy comes from the retained projection, independently of
    # whether the full view packs columns, records or excerpts into fewer lines.
    depths: dict[str, int] = {}
    pending = [(node, 0) for node in reversed(writer.request.projection["roots"])]
    while pending:
        node, level = pending.pop()
        depths[node["ref"]] = level
        pending.extend((child, level + 1) for child in reversed(node["children"]))
    offset = 0
    while offset < len(block.children):
        key = block.children[offset]
        group = groups.get(key)
        if group is None:
            writer.write(key, depth, context)
            offset += 1
            continue
        if block.children[offset : offset + len(group.child_keys)] != group.child_keys:
            raise ValueError("Content overview no longer matches its selected child range")
        lines = format_group_overview(group)
        writer.lines.extend("\t" * depth + line for line in lines)
        group_context = (*context, (group.section_key, "\t" * depth + lines[0]))
        if isinstance(group, BackgroundOverview):
            start = len(writer.lines)
            for child in group.child_keys:
                writer.write(child, depth, group_context)
            writer.background_content[group.section_key] = tuple(writer.lines[start:])
            del writer.lines[start:]
            if group.important_refs:
                writer.lines.append("\t" * depth + "Background state:")
            for ref in group.important_refs:
                writer.lines.extend(writer.nodes[ref].text.splitlines())
            for ref in group.uncertain_refs:
                for hint in writer.coverage_lines(writer.atoms[ref]):
                    writer.lines.append("\t" * depth + f"Element {ref}: " + hint)
        else:
            for ref in group.projected_refs:
                writer.remember(ref, depths[ref], group_context)
        writer.group_sections.append(group.section_key)
        offset += len(group.child_keys)
