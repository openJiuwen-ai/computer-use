# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Find complete form spans through transparent wrappers while retaining every source."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from computer_use.interaction.read.presentation.interface.helpers import element_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft


@dataclass(frozen=True)
class MemberSpan:
    """An existing parent and a contiguous interval of complete child blocks."""

    parent: str
    start: int
    stop: int
    children: tuple[str, ...]


def _transparent(ref: str, draft: InterfaceDraft) -> bool:
    """Require affirmative passive facts before crossing a layout-only wrapper."""
    node = draft.projected_nodes[ref]
    caps = node.get("capabilities")
    return (
        node["role"] == "group"
        and not draft.evidence[ref].incomplete
        and not draft.evidence[ref].hidden
        and not draft.evidence[ref].independent
        and isinstance(caps, Mapping)
        and bool(caps)
        and all(value == "unavailable" for value in caps.values())
        and "actions" in node
        and not node["actions"]
        and not node.get("actionsUnavailable")
        and not any(
            node.get(name)
            for name in (
                "name",
                "description",
                "value",
                "help",
                "placeholder",
                "selectedText",
                "modal",
                "busy",
                "invalid",
                "preserveGroup",
            )
        )
    )


def member_span(refs: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> MemberSpan | None:
    """Find the smallest source-complete sibling interval containing the given members.

    Related fields can pass through proven passive wrappers. Any unrelated content,
    independent control, region boundary or incomplete wrapper keeps the existing
    hierarchy. Every wrapper stays in the returned complete child blocks.
    """
    keys = tuple(element_key(ref) for ref in refs)
    parents = {child: key for key, block in blocks.items() for child in block.children}
    paths = []
    for key in keys:
        path = [key]
        while path[-1] in parents:
            path.append(parents[path[-1]])
        paths.append(path)
    common = next((key for key in paths[0][1:] if all(key in path[1:] for path in paths)), None)
    if common is None:
        return None
    parent = blocks[common]
    roots = {path[path.index(common) - 1] for path in paths}
    indices = sorted(parent.children.index(key) for key in roots)
    selected = parent.children[indices[0] : indices[-1] + 1]
    if len(selected) != len(roots):
        return None
    required = set(refs)
    pending = list(selected)
    encountered: set[str] = set()
    while pending:
        block = blocks[pending.pop()]
        if block.primary_ref is None:
            return None
        for ref in block.source_refs:
            if ref in required:
                encountered.add(ref)
            elif not _transparent(ref, draft):
                return None
        pending.extend(block.children)
    if encountered != required:
        return None
    return MemberSpan(common, indices[0], indices[-1] + 1, selected)
