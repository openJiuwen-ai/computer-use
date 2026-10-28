# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Measure whole reading units without treating their descendants as peers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import unicodedata

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import block_bounds, block_sources
from computer_use.interaction.read.presentation.spatial.geometry import Box


_LOGICAL_ROLES = frozenset(
    ("list", "outline", "table", "row", "cell", "column", "document", "menu", "menuBar", "toolbar", "tabGroup")
)
_DIRECTION_FIELDS = ("name", "value", "description", "help", "placeholder")


def source_only(block: InterfaceBlock) -> bool:
    """Identify a retained passive wrapper that occupies no reading position."""
    return block.layout == "source-only"


def _ordered_outside_unit(ref: str, draft: InterfaceDraft) -> bool:
    """Protect logical ancestors while allowing a whole collection to move as a pane.

    A table's own rectangle can establish its position among sibling panes. Its
    rows remain under that table and retain their logical identity. Web content
    is a spatial scope; its child enumeration alone declares no reading order.
    """
    current = draft.source.parents.get(ref)
    seen = {ref}
    while current is not None and current not in seen:
        seen.add(current)
        node = draft.source.native_nodes.get(current)
        if node is None:
            return True
        if node["role"] in _LOGICAL_ROLES:
            return True
        if node["role"] in ("window", "sheet"):
            return False
        current = draft.source.parents.get(current)
    return current is not None


def measured_units(keys: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> dict[str, Box]:
    """Return independently placeable whole units inside their captured window.

    Args:
        keys: Direct child blocks of one proposed layout scope.
        draft: Immutable facts and source identities for this presentation.
        blocks: Current local layout, including already composed controls.

    Returns:
        Rectangles for individually supported units. Unknown, hidden and conflicting
        units remain absent from this map so the caller can retain local barriers.
        Descendant completeness and independent inner scroll scopes do not remove
        an otherwise measured pane from its parent's spatial analysis.
    """
    result: dict[str, Box] = {}
    for key in keys:
        block = blocks[key]
        if source_only(block):
            continue
        refs = block_sources(key, blocks)
        if not refs:
            continue
        sources = draft.geometric_sources
        if sources is not None and not set(refs) <= sources.allowed_layout_refs:
            continue
        anchor = block.primary_ref or refs[0]
        node = draft.projected_nodes[anchor]
        evidence = draft.evidence[anchor]
        if (
            evidence.hidden
            or node.get("modal") is True
            or node.get("subrole")
            in (
                "dialog",
                "systemDialog",
                "popover",
                "closeButton",
                "minimizeButton",
                "zoomButton",
                "fullScreenButton",
                "close",
                "minimize",
                "zoom",
                "fullScreen",
            )
            or node["role"] in ("closeButton", "minimizeButton", "zoomButton")
            or _ordered_outside_unit(anchor, draft)
        ):
            continue
        # The unit keeps all its inner identities. Its captured outer scope must
        # nevertheless agree: this never combines objects from different windows.
        signatures = {
            (draft.evidence[ref].scope, tuple(draft.projected_nodes[ref].get("blockedBy", ()))) for ref in refs
        }
        if len(signatures) != 1:
            continue
        if any(
            isinstance(node.get(field), str)
            and any(unicodedata.bidirectional(char) in ("R", "AL", "RLE", "RLO", "RLI") for char in node[field])
            for field in _DIRECTION_FIELDS
        ):
            continue
        box = block_bounds(key, draft, blocks)
        if box is not None:
            result[key] = box
    # Providers can collapse several off-viewport objects onto one clipped frame.
    # A valid identical rectangle supplies no relative position between them.
    # Retain those identities as local source-order barriers rather than sorting
    # them by an implementation key or a coincident alignment tie breaker.
    coincident: dict[Box, list[str]] = {}
    for key, box in result.items():
        coincident.setdefault(box, []).append(key)
    ambiguous = {key for keys_at_box in coincident.values() if len(keys_at_box) > 1 for key in keys_at_box}
    result = {key: box for key, box in result.items() if key not in ambiguous}
    return result
