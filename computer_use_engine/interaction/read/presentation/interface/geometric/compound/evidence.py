# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check original child paths and comparable bounds for visual compound controls."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.spatial.geometry import Box


CONTROL_ROLES = frozenset(
    ("button", "link", "tab", "menuButton", "popUpButton", "checkBox", "radioButton", "switch", "disclosureTriangle")
)
MEMBER_ROLES = CONTROL_ROLES | {"group", "staticText", "image"}


def complete_children(draft: InterfaceDraft, ref: str) -> tuple[str, ...] | None:
    """Read exact complete child membership without treating unknown as empty."""
    node, original = draft.projected_nodes[ref], draft.source.native_nodes.get(ref)
    if original is None or draft.evidence[ref].incomplete:
        return None
    projected = tuple(node.get("children", ()))
    native = tuple(child["ref"] for child in original.get("children", ()))
    if projected != native:
        return None
    source = draft.geometric_sources
    if source is None or source.native is None:
        return projected
    token = source.ref_to_native.get(ref)
    relations = tuple(
        item for item in source.facts.get("relations", ()) if item["source"] == token and item["kind"] == "children"
    )
    if not relations:
        return None
    expected = tuple(source.ref_to_native.get(child) for child in projected)
    for edge in relations:
        targets = tuple(edge["targets"])
        if (
            edge["certainty"] != "confirmed"
            or edge["state"] not in ("complete", "absent")
            or edge.get("enumeration", "enumerated") != "enumerated"
            or edge.get("offset", 0) != 0
            or edge.get("total", len(targets)) != len(targets)
            or tuple(edge.get("positions", range(len(targets)))) != tuple(range(len(targets)))
            or targets != expected
        ):
            return None
    return projected


def confirmed_edge(draft: InterfaceDraft, parent: str, child: str) -> bool:
    """Require one original projected/native parent edge within the same scope."""
    if (
        draft.projected_parents.get(child) != parent
        or draft.source.parents.get(child) != parent
        or draft.source.scopes.get(child) != draft.source.scopes.get(parent)
    ):
        return False
    source = draft.geometric_sources
    if source is not None:
        report = source.parent_reports.get(child)
        if report is None or report.status not in ("confirmed", "semantic") or report.parent != parent:
            return False
    return True


def close_wrapper(wrapper: Box, target: Box) -> bool:
    """Accept nearly coincident control bounds with modest native padding."""
    if not wrapper.comparable(target) or wrapper.overlap(target) < 0.95:
        return False
    return (
        max(wrapper.area, target.area) / min(wrapper.area, target.area) <= 1.5
        and max(wrapper.width, target.width) / min(wrapper.width, target.width) <= 1.5
        and max(wrapper.height, target.height) / min(wrapper.height, target.height) <= 1.5
    )


def contained_caption(caption: Box, target: Box) -> bool:
    """Keep inside captions and small native padding in the same control unit."""
    if not caption.comparable(target):
        return False
    padding = 0.1 * min(target.width, target.height)
    return (
        caption.x >= target.x - padding
        and caption.y >= target.y - padding
        and caption.right <= target.right + padding
        and caption.bottom <= target.bottom + padding
    )


def one_window(draft: InterfaceDraft, refs: tuple[str, ...]) -> bool:
    """Reject conflicting captured window reports while preserving unknown reports."""
    if len({draft.source.scopes.get(ref) for ref in refs}) != 1:
        return False
    if len({tuple(draft.projected_nodes[ref].get("blockedBy", ())) for ref in refs}) != 1:
        return False
    source = draft.geometric_sources
    if source is None or source.native is None:
        return True
    windows = set()
    for ref in refs:
        native = source.native_node(ref)
        if native is None:
            return False
        for relation in native.relations.values():
            if relation.kind == "window":
                windows.update(relation.targets)
    return len(windows) <= 1
