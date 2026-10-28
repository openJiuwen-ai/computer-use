# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source scopes and comparable bounds used by local region decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import unicodedata

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.spatial.geometry import Box


ORDERED_ROLES = frozenset(
    (
        "list",
        "outline",
        "table",
        "row",
        "cell",
        "column",
        "document",
        "webArea",
        "menu",
        "menuBar",
        "toolbar",
        "tabGroup",
    )
)
_WINDOW_ROLES = frozenset(("window", "sheet"))
_DIRECTION_FIELDS = ("name", "value", "description", "help", "placeholder")


def _original_node(ref: str, draft: InterfaceDraft) -> Mapping[str, object] | None:
    """Query pre-compression facts, including ancestors outside displayed blocks."""
    sources = draft.geometric_sources
    return sources.semantic_nodes.get(ref) if sources is not None else draft.source.native_nodes.get(ref)


def _original_parent(ref: str, draft: InterfaceDraft) -> tuple[str | None, bool]:
    """Return an original edge and whether its evidence permits ancestor traversal."""
    sources = draft.geometric_sources
    if sources is None:
        return draft.source.parents.get(ref), True
    report = sources.parent_reports.get(ref)
    if report is None or report.status in ("unknown", "conflict"):
        return None, False
    return report.parent, True


def block_sources(key: str, blocks: Mapping[str, InterfaceBlock]) -> tuple[str, ...]:
    """Return real sources below one complete reading unit in its existing order."""
    refs: list[str] = []
    pending = [key]
    while pending:
        block = blocks[pending.pop()]
        refs.extend(block.source_refs)
        pending.extend(reversed(block.children))
    return tuple(refs)


def block_bounds(key: str, draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]) -> Box | None:
    """Measure one whole unit without mixing its wrapper and descendants as peers."""
    block = blocks[key]
    if block.primary_ref is not None:
        return draft.source.geometry.boxes.get(block.primary_ref)
    boxes = [block_bounds(child, draft, blocks) for child in block.children]
    if not boxes or any(box is None for box in boxes):
        return None
    measured = tuple(box for box in boxes if box is not None)
    if len({box.space for box in measured}) != 1:
        return None
    left, top = min(box.x for box in measured), min(box.y for box in measured)
    return Box(
        measured[0].space,
        left,
        top,
        max(box.right for box in measured) - left,
        max(box.bottom for box in measured) - top,
    )


def ordered_ancestry(ref: str, draft: InterfaceDraft) -> bool:
    """Protect logical sequences and ancestry that cannot establish a local scope."""
    current: str | None = ref
    seen = set()
    while current is not None:
        node = _original_node(current, draft)
        if node is None or current in seen:
            return True
        seen.add(current)
        if node["role"] in ORDERED_ROLES:
            return True
        # A known window bounds this layout query. Its unknown application-level
        # ancestor does not invalidate complete local controls inside the window.
        if draft.geometric_sources is not None and node["role"] in _WINDOW_ROLES:
            return False
        current, confirmed = _original_parent(current, draft)
        if not confirmed:
            return True
    return False


def reading_scope(ref: str, draft: InterfaceDraft) -> tuple[str, ...]:
    """Identify original window, modal and scroll boundaries independently of layout."""
    scopes = []
    current: str | None = ref
    seen = set()
    while current is not None:
        node = _original_node(current, draft)
        if node is None or current in seen:
            scopes.append("unresolved:" + current)
            break
        seen.add(current)
        if (
            node["role"] in _WINDOW_ROLES | {"scrollArea"}
            or node.get("modal") is True
            or node.get("subrole") in ("dialog", "systemDialog", "popover")
        ):
            scopes.append(current)
        if draft.geometric_sources is not None and node["role"] in _WINDOW_ROLES:
            break
        parent, confirmed = _original_parent(current, draft)
        if not confirmed:
            scopes.append("unresolved:" + current)
            break
        current = parent
    return tuple(scopes)


def comparable_members(
    keys: Sequence[str], draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock]
) -> dict[str, Box] | None:
    """Accept complete local geometry while retaining ambiguous directions and scopes.

    Right-to-left text only inhibits geometric reordering: it does not infer an
    application writing direction. Native flow declarations remain authoritative.
    """
    refs = tuple(ref for key in keys for ref in block_sources(key, blocks))
    if draft.geometric_sources is not None and not set(refs) <= draft.geometric_sources.allowed_layout_refs:
        return None
    if not refs or any(
        draft.evidence[ref].hidden or draft.evidence[ref].incomplete or ordered_ancestry(ref, draft) for ref in refs
    ):
        return None
    if (
        len(
            {
                (
                    draft.evidence[ref].scope,
                    reading_scope(ref, draft),
                    tuple(draft.projected_nodes[ref].get("blockedBy", ())),
                )
                for ref in refs
            }
        )
        != 1
    ):
        return None
    for ref in refs:
        node = _original_node(ref, draft)
        if node is None:
            return None
        if any(
            isinstance(node.get(field), str)
            and any(unicodedata.bidirectional(char) in ("R", "AL", "RLE", "RLO", "RLI") for char in node[field])
            for field in _DIRECTION_FIELDS
        ):
            return None
    bounds = {key: block_bounds(key, draft, blocks) for key in keys}
    if any(box is None for box in bounds.values()):
        return None
    measured = {key: box for key, box in bounds.items() if box is not None}
    if len({box.space for box in measured.values()}) != 1:
        return None
    return measured


def linked_members(owners: Mapping[str, str], draft: InterfaceDraft) -> bool:
    """Protect not-yet-composed labels and fields from separate geometric moves."""
    for ref, owner in owners.items():
        node = draft.projected_nodes[ref]
        linked = (node.get("titleRef"), node.get("visualLabelRef"), *node.get("labelTargets", ()))
        if any(isinstance(target, str) and target in owners and owners[target] != owner for target in linked):
            return True
    return any(
        relation.kind in ("labelled_by", "labels", "described_by", "describes")
        and relation.source in owners
        and any(target in owners and owners[target] != owners[relation.source] for target in relation.targets)
        for relation in draft.relations
    )
