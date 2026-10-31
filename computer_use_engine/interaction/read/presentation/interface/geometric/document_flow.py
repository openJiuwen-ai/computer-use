# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize source-supported prose flow independently of WebView implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources


def _child_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
    """Read immutable semantic children in the exact captured order."""
    return tuple(child["ref"] if isinstance(child, Mapping) else child for child in node.get("children", ()))


def _unwrap(ref: str, draft: InterfaceDraft) -> Mapping[str, Any] | None:
    """Follow a passive single-child wrapper without flattening separate cards."""
    seen = set()
    while ref not in seen:
        seen.add(ref)
        node = draft.source.native_nodes.get(ref)
        if node is None:
            return None
        children = _child_refs(node)
        if (
            node["role"] not in ("group", "webArea")
            or len(children) != 1
            or node.get("actions")
            or node.get("selected")
            or node.get("editable")
        ):
            return node
        ref = children[0]
    return None


def _declares_prose(ref: str, draft: InterfaceDraft) -> bool:
    """Recognize direct heading/body sequences while preserving card boundaries."""
    scope = _unwrap(ref, draft)
    if scope is None:
        return False
    children = tuple(_unwrap(child, draft) for child in _child_refs(scope))
    children = tuple(child for child in children if child is not None)
    if sum(child["role"] == "heading" for child in children) < 2:
        return False
    # A document outline is heading + prose/list/code. Native selectable rows,
    # form controls and app navigation establish a different local structure.
    allowed = {"heading", "staticText", "paragraph", "list", "code", "group"}
    if any(child["role"] not in allowed for child in children):
        return False
    return any(child["role"] in ("staticText", "paragraph", "list", "code") for child in children)


def document_flow_scope(key: str, draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> bool:
    """Keep captured paragraph/section order inside an evidenced prose surface.

    Args:
        key: Candidate parent block whose children might otherwise be reordered.
        draft: Immutable native semantics and original ancestry.
        blocks: Current source-preserving display blocks.

    Returns:
        Whether this scope lies inside a direct native heading/body sequence.
        Whole document panes can still be placed relative to surrounding app panes.
    """
    block = blocks[key]
    refs = block_sources(key, blocks)
    current = block.primary_ref or block.label_ref or (refs[0] if refs else None)
    seen = set()
    while current is not None and current not in seen:
        seen.add(current)
        node = draft.source.native_nodes.get(current)
        if node is None:
            return False
        if node["role"] == "document" or _declares_prose(current, draft):
            return True
        if node["role"] in ("window", "sheet"):
            return False
        current = draft.source.parents.get(current)
    return False
