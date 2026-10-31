# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Declare identical scoped secondary-action records once in compact full output."""

from __future__ import annotations

from dataclasses import replace
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import NodeView

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.actions.catalogue import ActionCatalogue
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def _without_actions(line: str, catalogue: ActionCatalogue) -> str | None:
    """Remove one proven inline field while preserving quoted content verbatim."""
    field = "Secondary Actions: " + "; ".join(catalogue.labels)
    if line.count(field) != 1:
        return None
    start = line.index(field)
    quoted = escaped = False
    for character in line[:start]:
        if escaped:
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
    end = start + len(field)
    if quoted or (line[end:] and not line[end:].startswith(", ")):
        return None
    prefix = line[:start]
    if prefix.endswith(", "):
        return prefix[:-2] + line[end:]
    if prefix.endswith(" "):
        return prefix.rstrip() + line[end:]
    return None


def group_sibling_actions(frame: LayoutFrame) -> list[str]:
    """Share equal secondary records below their nearest common display ancestor.

    Each element keeps its numbered line and independent diff record. A shared
    declaration names every exact source, stays within its captured and displayed
    window/menu scope, and must shorten the combined text. Full native catalogues
    establish source completeness; only the displayed records are shared.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef") or not frame.action_sources:
        return frame.lines
    positions: dict[str, tuple[int, str]] = {}
    duplicates = set()
    for index, line in enumerate(frame.lines):
        match = re.match(r"^(\t*)([0-9]+) ", line)
        if match:
            if match[2] in positions:
                duplicates.add(match[2])
            positions[match[2]] = (index, match[1])
    for ref in duplicates:
        del positions[ref]
    replacements: dict[int, str] = {}
    declarations: dict[int, list[str]] = {}
    groups: dict[tuple[str, str], list[tuple[str, int, str, ActionCatalogue]]] = {}
    ancestors: dict[str, tuple[str, ...]] = {}
    nodes: dict[str, NodeView] = {}
    pending = [(node, (), node["ref"]) for node in reversed(frame.roots)]
    while pending:
        node, path, scope = pending.pop()
        ref = node["ref"]
        position = positions.get(ref)
        parent_position = positions.get(path[-1]) if path else None
        if position is None or path and (parent_position is None or position[1] != parent_position[1] + "\t"):
            continue
        if node["role"] in ("window", "sheet", "menuBar", "menu"):
            scope = ref
        ancestors[ref] = (*path, ref)
        nodes[ref] = node
        pending.extend((child, ancestors[ref], scope) for child in reversed(node["children"]))
        source = frame.action_sources.get(ref)
        if source is not None:
            stripped = _without_actions(frame.lines[position[0]], source)
            if stripped is not None:
                groups.setdefault((scope, source.key), []).append((ref, position[0], stripped, source))
    for members in groups.values():
        if len(members) < 2:
            continue
        shared_path = ancestors[members[0][0]]
        for ref, *_ in members[1:]:
            path = ancestors[ref]
            length = next(
                (index for index, pair in enumerate(zip(shared_path, path)) if pair[0] != pair[1]),
                min(len(shared_path), len(path)),
            )
            shared_path = shared_path[:length]
        if not shared_path:
            continue
        parent = nodes[shared_path[-1]]
        parent_position = positions[parent["ref"]]
        children = [child["ref"] for child in parent["children"]]
        if not children or any(
            ref not in positions or positions[ref][1] != parent_position[1] + "\t" for ref in children
        ):
            continue
        source = members[0][3]
        # These are the same callable labels already shown on each member.
        # The node formatter includes the native key whenever a description
        # collides with another action's name or description on that source.
        actions = source.labels
        declaration = (
            parent_position[1]
            + "\tSecondary Actions [elements "
            + reference_set(member[0] for member in members)
            + "]: "
            + "; ".join(actions)
        )
        removed = sum(len(frame.lines[index]) - len(stripped) for _, index, stripped, _ in members)
        if removed <= len(declaration) + 1:
            continue
        declarations.setdefault(min(positions[ref][0] for ref in children), []).append(declaration)
        replacements.update((index, stripped) for _, index, stripped, _ in members)
    output: list[str] = []
    for index, line in enumerate(frame.lines):
        output.extend(declarations.get(index, ()))
        output.append(replacements.get(index, line))
    return output


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Apply source-aware grouping without changing semantic or diff records."""
    lines = group_sibling_actions(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_sibling_actions", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_sibling_actions",
    function=execute,
    contract=Contract(
        LayoutFrame, LayoutFrame, requires=("complete native action evidence",), writes=("packed lines",)
    ),
)
