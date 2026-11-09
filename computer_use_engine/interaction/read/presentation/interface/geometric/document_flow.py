# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize source-supported prose flow independently of WebView implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import block_sources

_source_indexes = WeakIdentityMap()
_PROSE_ROLES = frozenset(("heading", "staticText", "paragraph", "list", "code", "group"))
_BODY_ROLES = frozenset(("staticText", "paragraph", "list", "code"))


def _child_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
    """Read immutable semantic children in the exact captured order."""
    return tuple(child["ref"] if isinstance(child, Mapping) else child for child in node.get("children", ()))


class _DocumentFlowIndex:
    """Reuse source-only predicates while display blocks change between passes.

    The weak registry keys the exact immutable ProjectionInput. Its value retains
    only the source maps, never that key or a display draft. Layout eligibility
    and each block's starting reference are still resolved from current blocks.
    """

    def __init__(self, source):
        self.nodes, self.parents = source.native_nodes, source.parents
        self.children, self.unwrapped, self.prose, self.scopes = {}, {}, {}, {}

    def _children(self, node):
        ref = node["ref"]
        if ref not in self.children:
            self.children[ref] = _child_refs(node)
        return self.children[ref]

    def _unwrap(self, ref):
        """Follow passive wrappers once, preserving cards and wrapper cycles."""
        path, seen = [], set()
        while ref not in self.unwrapped and ref not in seen:
            seen.add(ref)
            path.append(ref)
            node = self.nodes.get(ref)
            if node is None:
                result = None
                break
            children = self._children(node)
            if node["role"] not in ("group", "webArea") or len(children) != 1:
                result = node
                break
            if node.get("actions") or node.get("selected") or node.get("editable"):
                result = node
                break
            ref = children[0]
        else:
            result = self.unwrapped.get(ref)
        for member in path:
            self.unwrapped[member] = result
        return result

    def _declares_prose(self, ref):
        """Recognize direct heading/body sequences without flattening cards."""
        if ref not in self.prose:
            scope = self._unwrap(ref)
            if scope is None:
                self.prose[ref] = False
            else:
                children = tuple(self._unwrap(child) for child in self._children(scope))
                roles = tuple(child["role"] for child in children if child is not None)
                self.prose[ref] = (
                    roles.count("heading") >= 2
                    and all(role in _PROSE_ROLES for role in roles)
                    and any(role in _BODY_ROLES for role in roles)
                )
        return self.prose[ref]

    def contains(self, ref):
        """Resolve immutable ancestry once, including negative and cyclic paths."""
        path, seen = [], set()
        while ref is not None and ref not in self.scopes and ref not in seen:
            seen.add(ref)
            path.append(ref)
            node = self.nodes.get(ref)
            if node is None:
                result = False
                break
            if node["role"] == "document" or self._declares_prose(ref):
                result = True
                break
            if node["role"] in ("window", "sheet"):
                result = False
                break
            ref = self.parents.get(ref)
        else:
            result = self.scopes.get(ref, False)
        for member in path:
            self.scopes[member] = result
        return result


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
    index = _source_indexes.get(draft.source)
    if index is None:
        index = _DocumentFlowIndex(draft.source)
        _source_indexes.set(draft.source, index)
    return index.contains(current)
