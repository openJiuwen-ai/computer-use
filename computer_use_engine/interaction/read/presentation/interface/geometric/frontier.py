# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expose layout children while retaining proved passive wrapper sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument, InterfaceDraft


_PASSIVE_CAPABILITIES = ("click", "typeText", "replaceText", "focus")
_CONTENT_FIELDS = (
    "name",
    "description",
    "value",
    "help",
    "placeholder",
    "selectedText",
    "documentText",
    "controlValue",
    "rangeValue",
    "url",
    "fullDescription",
    "valueDescription",
    "rangeDescription",
    "shortcut",
    "landmarkType",
    "localizedLandmarkType",
)
_OWNED_STATE = (
    "focused",
    "selected",
    "selectable",
    "editable",
    "pressable",
    "focusable",
    "busy",
    "invalid",
    "modal",
    "expandable",
    "expanded",
    "submenu",
    "sensitive",
    "protected",
    "preserveGroup",
    "incompleteLabelRelation",
    "actionsUnavailable",
    "linkedLabel",
    "titleRef",
    "visualLabelRef",
    "labelTargets",
    "errorRelations",
    "toggleState",
    "menuMark",
    "required",
    "unsaved",
    "readOnly",
    "textReadOnly",
    "rangeReadOnly",
    "disclosedBy",
)
_STRUCTURAL_FIELDS = (
    "headingLevel",
    "rowIndex",
    "columnIndex",
    "rowRange",
    "columnRange",
    "rowCount",
    "columnCount",
    "cellPosition",
    "cellRow",
    "cellColumn",
    "cellRowSpan",
    "cellColumnSpan",
    "selectedChildCount",
    "selectionRange",
    "documentReports",
    "documentUrl",
    "disclosureLevel",
    "layoutGroups",
)


def transparent_group(ref: str, draft: InterfaceDraft | InterfaceDocument) -> bool:
    """Recognize an empty implementation group with affirmative passive evidence.

    Args:
        ref: Original projected reference of the candidate wrapper.
        draft: Draft or frozen document retaining exact source fields and relationships.

    Returns:
        Whether the group's children may participate in its parent's visual layout.
        The original group and all its fields remain available under their own ref.
    """
    node = draft.projected_nodes[ref]
    original = draft.source.native_nodes.get(ref)
    if original is None:
        return False
    read = original.get("childrenRead", len(original.get("children", ())))
    total = original.get("childCount", read)
    incomplete = (
        node.get("partialReason") is not None
        or original.get("partialReason") is not None
        or isinstance(read, int)
        and isinstance(total, int)
        and read < total
    )
    hidden = node.get("visible") is False or node.get("showing") is False or node.get("offscreen") is True
    caps = node.get("capabilities")
    if (
        node["role"] != "group"
        or incomplete
        or hidden
        or not isinstance(caps, Mapping)
        or not caps
        or any(caps.get(key) != "unavailable" for key in _PASSIVE_CAPABILITIES)
        or any(value != "unavailable" for value in caps.values())
        or "actions" not in node
        or node["actions"]
        or node.get("enabled") is False
        or node.get("subrole") not in (None, "", "hostingView")
        or node.get("roleDescription") not in (None, "", "group", "组", "container")
        or any(node.get(key) is not None and node.get(key) != "" for key in _CONTENT_FIELDS)
        or any(node.get(key) for key in _OWNED_STATE)
        or any(key in node for key in _STRUCTURAL_FIELDS)
    ):
        return False
    children = tuple(child["ref"] for child in original.get("children", ()))
    if not children or any(child not in draft.projected_nodes for child in children):
        return False
    if (
        len(
            {
                (draft.source.scopes.get(member), tuple(draft.projected_nodes[member].get("blockedBy", ())))
                for member in (ref, *children)
            }
        )
        != 1
    ):
        return False
    sources = draft.geometric_sources
    if sources is not None:
        for child in children:
            report = sources.parent_reports.get(child)
            if report is None or report.parent != ref or report.status not in ("confirmed", "semantic"):
                return False
        own = sources.parent_reports.get(ref)
        if own is None or own.status not in ("confirmed", "semantic"):
            return False
    if any(
        relation.kind not in ("children", "parent", "window")
        and (relation.source == ref and relation.targets or ref in relation.targets)
        for relation in draft.relations
    ):
        return False
    return True


def expand_transparent_frontier(draft: InterfaceDraft) -> InterfaceDraft:
    """Lift proved layout-only children into a comparable source-preserving frontier.

    Args:
        draft: Current block graph before geometric row and region analysis.

    Returns:
        A new graph whose passive wrappers are retained as source-only siblings.
        Controls and composed fields keep their own inner structure. Original
        source parents, aliases and fields are unchanged.
    """
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions: list[str] = []
    order: list[str] = []
    pending = list(reversed(draft.roots))
    while pending:
        key = pending.pop()
        order.append(key)
        pending.extend(reversed(blocks[key].children))
    protected: set[str] = set()
    for block in blocks.values():
        if not block.visual_unit:
            continue
        members = [block.key]
        while members:
            current = members.pop()
            protected.add(current)
            members.extend(blocks[current].children)
    for key in reversed(order):
        parent = blocks[key]
        role = draft.projected_nodes[parent.primary_ref]["role"] if parent.primary_ref is not None else None
        if (
            key in protected
            or parent.kind in ("control", "field", "collection", "table", "record")
            or role in ("list", "outline", "table", "row", "cell", "column", "menu", "menuBar", "document")
        ):
            continue
        children: list[str] = []
        retained: list[str] = []
        for child_key in parent.children:
            child = blocks[child_key]
            ref = child.primary_ref
            if (
                ref is None
                or child.source_refs != (ref,)
                or not child.children
                or child.layout != "tree"
                or not transparent_group(ref, draft)
            ):
                children.append(child_key)
                continue
            children.extend(child.children)
            retained.append(child_key)
            blocks[child_key] = replace(child, children=(), layout="source-only")
            decisions.append("geometric-transparent-frontier:" + ref)
        if retained:
            blocks[key] = replace(parent, children=(*children, *retained))
    return revise(draft, blocks, *decisions)
