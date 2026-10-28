# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.descriptions import shared_description

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode
    from computer_use.protocol.records import Record
from computer_use.interaction.read.presentation.compression.captions import passive_label
from computer_use.interaction.read.presentation.compression.policy import (
    layout,
    ordinary_control,
    represented,
    same_blocking,
    sources,
)
from computer_use.interaction.read.presentation.tree import (
    VALUE_PREVIEW_LIMIT,
    disclosure,
    has_incomplete_children,
    is_dialog,
)
from computer_use.native.semantics import TABLE_FIELDS
from computer_use.protocol.primitives import utf16_length


def member_layout(
    state: ProjectionView,
    edit: ProjectionEditor,
    node: ViewNode,
    target: ViewNode,
    ignore_focus: bool = False,
    ignore_selected: bool = False,
) -> bool:
    """Member Layout.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
        target: Source/projected node in this exact view; its native identity is unchanged.
    """
    if node.get("_factBacked") and node["role"] == "cell" and target["role"] == "row" and state.member_of(node, target):
        # Single-column item cells can contribute captions despite their own
        # coordinates/focus. compactLabel separately preserves those fields
        # in the public output when deciding whether to fold the cell.
        # The control-label pass excludes table rows and multi-cell records.
        if any(node.get(key, {}).get("length", 1) != 1 for key in ("rowRange", "columnRange")):
            return False
        candidate = {key: value for key, value in node.items() if key not in ("rowRange", "columnRange", "index")}
        if candidate.get("titleRef") in state.nodes and state.member_of(state.nodes[candidate["titleRef"]], target):
            candidate.pop("titleRef")
        return layout(candidate, ignore_focus, ignore_selected)
    return layout(node, ignore_focus, ignore_selected)


def label_parts(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, target: ViewNode) -> list[Record] | None:
    """Label Parts.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
        target: Source/projected node in this exact view; its native identity is unchanged.
    """
    if (
        target["role"] == "row"
        and node["role"] == "cell"
        and node.get("titleRef")
        and state.ancestor_of(node["ref"], node["titleRef"])
        and not node.get("incompleteLabelRelation")
        and not node.get("valueExpected")
        and "value" not in node
        and same_blocking(node, target)
        and not node.get("modal")
        and not is_dialog(node)
    ):
        title = state.nodes.get(node["titleRef"])
        if (
            title is not None
            and not title.get("incompleteLabelRelation")
            and same_blocking(title, target)
            and len(state.targets.get(title["ref"], {})) == 1
            and node["ref"] in state.targets[title["ref"]]
            and utf16_length(title.get("value", "")) <= VALUE_PREVIEW_LIMIT
        ):
            return sources(title)
    if "capabilities" in target and not state.member_of(node, target):
        return None
    if (
        node["role"] == "staticText"
        and not any(
            node.get(k)
            for k in (
                "pressable",
                "selectable",
                "focusable",
                "valueExpected",
                "incompleteLabelRelation",
                "labelTargets",
            )
        )
        and not node["children"]
        and "partialReason" not in node
        and utf16_length(node.get("value", "")) <= VALUE_PREVIEW_LIMIT
    ):
        return sources(node)
    if (
        node["role"] == "image"
        and not any(node.get(k) for k in ("pressable", "selectable", "focusable", "actions", "actionsUnavailable"))
        and not node["children"]
        and "partialReason" not in node
    ):
        return []
    if not member_layout(state, edit, node, target, True, True):
        return None
    result = []
    for child in node["children"]:
        parts = label_parts(state, edit, child, target)
        if parts is None:
            return None
        result.extend(parts)
    return result


def compact_label(
    state: ProjectionView, edit: ProjectionEditor, node: ViewNode, target: ViewNode
) -> list[ViewNode] | tuple[ViewNode, ...]:
    """Fold caption members while retaining independently reported cell facts.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
        target: Source/projected node in this exact view; its native identity is unchanged.
    """
    if "capabilities" in target and not state.member_of(node, target):
        return [node]
    if passive_label(state, edit, node, target) and not node["children"] and represented(node, target):
        edit.fold(node, target, "label")
        return []
    declaredLayout = any(
        m["ref"] == node["ref"] and m["part"] == "layout" for m in target.get("control", {}).get("members", [])
    )
    if (
        (not declaredLayout and label_parts(state, edit, node, target) is None)
        or not member_layout(state, edit, node, target, True, True)
        or state.keep(node)
        or not same_blocking(node, target)
    ):
        return [node]
    repeatedSelection = (
        node["role"] == "cell"
        and target["role"] == "row"
        and node.get("selected") is True
        and target.get("selected") is True
    )
    internalFocus = (
        node["role"] == "cell"
        and ordinary_control(target)
        and not (node["ref"] == state.context.get("focusedRef") and node.get("focused") is False)
    )
    edit.children(node, [c for child in node["children"] for c in compact_label(state, edit, child, target)])
    # Detailed output emits residual source fields as supplements. Compact
    # output needs the cell itself to expose its focus and structural position;
    # the row's coincident values belong to a different native object.
    if (
        state.context.get("compact")
        and node.get("_factBacked")
        and node["role"] == "cell"
        and target["role"] == "row"
        and (node.get("focused") is True or any(field in node for field in ("rowRange", "columnRange", "index")))
    ):
        return [node]
    if member_layout(state, edit, node, target, internalFocus, repeatedSelection) and (
        node["ref"] != state.context.get("focusedRef") or internalFocus
    ):
        edit.fold(node, target, "layout")
        return node["children"]
    return [node]


def compact_input(
    state: ProjectionView, edit: ProjectionEditor, node: ViewNode, owner: ViewNode
) -> list[ViewNode] | tuple[ViewNode, ...]:
    """Compact Input.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
        owner: Source/projected node in this exact view; its native identity is unchanged.
    """
    member = next((m for m in owner.get("control", {}).get("members", []) if m["ref"] == node["ref"]), None)
    if member is None or "control" in node or state.keep(node) or not same_blocking(node, owner):
        return [node]
    edit.children(node, [c for child in node["children"] for c in compact_input(state, edit, child, owner)])
    ownerText = {owner.get("name"), owner.get("description"), owner.get("value")}
    if (
        any(node.get(k) and node[k] not in ownerText for k in ("name", "description"))
        or ("value" in node and node["value"] != owner.get("value"))
        or ("value" in node and bool(node.get("valueIsNumber")) != bool(owner.get("valueIsNumber")))
        or (node.get("valueExpected") and "value" not in node)
        or (node.get("selectable") and not owner.get("selectable"))
        or (
            node.get("pressable")
            and not owner.get("pressable")
            and node.get("capabilities", {}).get("click") != owner.get("capabilities", {}).get("click")
        )
        or any(
            v == "available" and owner.get("capabilities", {}).get(k) != "available"
            for k, v in node.get("capabilities", {}).items()
        )
        or any(node.get(k) for k in ("actions", "actionsUnavailable", "help", "url"))
        or any(k in node and not shared_description(node, k) for k in ("valueDescription", "rangeDescription"))
        or any(node.get(k) for k in ("headingLevel", "landmarkType", "localizedLandmarkType", "fullDescription"))
        or (node.get("placeholder") and node["placeholder"] != owner.get("placeholder"))
        or has_incomplete_children(node)
        or any(node.get(k) for k in ("labelTargets", "incompleteLabelRelation", "preserveGroup", "subrole", "modal"))
        or ("selected" in node and node["selected"] != owner.get("selected"))
        or (node.get("enabled") is False and owner.get("enabled") is not False)
        or node.get("expandable")
        or node.get("submenu")
        or disclosure(node) is not None
        or "shortcut" in node
        or "menuMark" in node
        or "toggleState" in node
        or any(key in node for key in TABLE_FIELDS)
        or (node["ref"] == state.context.get("focusedRef") and node.get("focused") is False)
    ):
        return [node]
    if "selectionRange" in node or "selectedText" in node:
        if (
            "value" not in owner
            or node.get("value") != owner["value"]
            or ("selectionRange" in owner and owner["selectionRange"] != node.get("selectionRange"))
            or ("selectedText" in owner and owner["selectedText"] != node.get("selectedText"))
        ):
            return [node]
        if "selectionRange" in node:
            edit.set_field(owner, "selectionRange", node["selectionRange"], source=node)
        if "selectedText" in node:
            edit.set_field(owner, "selectedText", node["selectedText"], source=node)
    edit.fold(node, owner, "editor" if member["part"] == "editor" else "layout")
    return node["children"]
