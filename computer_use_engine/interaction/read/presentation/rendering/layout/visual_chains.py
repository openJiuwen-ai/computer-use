# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render spatially evidenced chains without changing their individual nodes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.rendering.layout.chains import (
    compact_tree_chains,
    expand_tree_chains,
)


def pack_visual_chains(lines: list[str], roots: Sequence[NodeView], context: Mapping[str, object]) -> list[str]:
    """Pack only marked compact-full chains when original lines remain recoverable.

    Structured nodes, source references and diff inputs remain untouched. Long
    lines and intervening field blocks keep their current layout. Unrecognized
    line syntax retains the complete original rendering.

    Args:
        lines: Final semantic lines before optional compact packing.
        roots: Display nodes containing spatially proven visualChainChild edges.
        context: Existing compact and subtree scope flags.

    Returns:
        Reversible packed lines, or the original lines when packing is unsuitable.
    """
    if not context.get("compact") or context.get("subtreeRef"):
        return lines
    selected: dict[str, str] = {}
    pending = list(roots)
    while pending:
        parent = pending.pop()
        children = parent["children"]
        target = parent.get("visualChainChild")
        if target is not None and len(children) == 1 and target == children[0]["ref"]:
            selected[parent["ref"]] = target
        pending.extend(children)
    if not selected:
        return lines
    result = compact_tree_chains(lines, roots, selected=selected)
    return result if expand_tree_chains(result) == lines else lines
