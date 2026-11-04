# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share equivalent initial child-coverage hints within their observed scope."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.rendering.layout.model import ChildCoverageHint, LayoutFrame
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def group_child_coverage(frame: LayoutFrame) -> list[str]:
    """Place equal generated hints at their nearest common displayed ancestor.

    Each declaration names every affected element. Original per-node hint records
    remain available to incremental delivery; source facts and cursors stay fixed.
    """
    if not frame.context.get("compact") or not frame.context.get("publicApi") or frame.context.get("subtreeRef"):
        return frame.lines
    nodes = {node.ref: node for node in frame.rendered_nodes}
    if len(nodes) != len(frame.rendered_nodes):
        return frame.lines
    roles: dict[str, str] = {}
    pending = list(frame.roots)
    while pending:
        node = pending.pop()
        roles[node["ref"]] = node["role"]
        pending.extend(node["children"])
    paths: dict[str, tuple[str, ...]] = {}
    scopes: dict[str, str] = {}
    # Formatter traversal is parent-first. Missing parents and invalid spans
    # leave that branch in its explicit per-element form.
    for node in frame.rendered_nodes:
        span = frame.node_line_ranges.get(node.ref)
        if (
            span is None
            or span[0] < 0
            or span[1] > len(frame.lines)
            or "\n".join(frame.lines[span[0] : span[1]]) != node.text
            or node.parent is not None
            and node.parent not in paths
        ):
            continue
        path = paths[node.parent] if node.parent is not None else ()
        paths[node.ref] = (*path, node.ref)
        scope = scopes[node.parent] if node.parent is not None else node.ref
        # Source scopes are checked separately; this also prevents regrouping
        # across a distinct window/menu in the displayed containment tree.
        scopes[node.ref] = scope
        if roles.get(node.ref) in ("window", "sheet", "menuBar", "menu"):
            scopes[node.ref] = node.ref
    groups: dict[tuple[str, str, int, str], list[ChildCoverageHint]] = defaultdict(list)
    used_refs: set[str] = set()
    for hint in frame.child_hints:
        node = nodes.get(hint.ref)
        span = frame.node_line_ranges.get(hint.ref)
        if (
            hint.ref in used_refs
            or hint.ref not in paths
            or node is None
            or span is None
            or not span[0] < hint.line_index < span[1]
            or frame.lines[hint.line_index] != hint.text
            or hint.text not in node.hints
            or hint.total <= 0
        ):
            continue
        expected = "\t" * (node.depth + len(node.layout) + 1) + f"Children: 0/{hint.total} read; partial; read()"
        if hint.text != expected:
            continue
        used_refs.add(hint.ref)
        groups[hint.scope, scopes[hint.ref], hint.total, hint.partial_reason].append(hint)
    removed: set[int] = set()
    inserted: dict[int, list[str]] = defaultdict(list)
    for members in groups.values():
        if len(members) < 2:
            continue
        shared: list[str] = []
        for level in zip(*(paths[item.ref] for item in members)):
            if len(set(level)) != 1:
                break
            shared.append(level[0])
        if not shared:
            continue
        parent = nodes[shared[-1]]
        insertion = frame.node_line_ranges[parent.ref][1]
        declaration = (
            "\t" * (parent.depth + len(parent.layout) + 1)
            + "Children [elements "
            + reference_set(item.ref for item in members)
            + f"]: 0/{members[0].total} read; partial; each read()"
        )
        if sum(len(item.text) + 1 for item in members) <= len(declaration) + 1:
            continue
        removed.update(item.line_index for item in members)
        inserted[insertion].append(declaration)
    result: list[str] = []
    for index, line in enumerate(frame.lines):
        result.extend(inserted.get(index, ()))
        if index not in removed:
            result.append(line)
    return result


def execute(frame: LayoutFrame, control: Control) -> LayoutFrame:
    """Replace repeated hints in compact full text while retaining per-node records."""
    lines = group_child_coverage(frame)
    records = frame.records
    if frame.context.get("recordStages"):
        records += (dict(name="group_child_coverage", beforeLines=len(frame.lines), afterLines=len(lines)),)
    return replace(frame, lines=lines, records=records)


stage = Pass.define(
    name="group_child_coverage",
    function=execute,
    contract=Contract(
        LayoutFrame,
        LayoutFrame,
        requires=("generated coverage hints", "original scopes", "unpacked node records"),
        writes=("packed lines",),
    ),
)
