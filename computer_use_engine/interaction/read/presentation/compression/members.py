# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.captions import passive_label
from computer_use.interaction.read.presentation.compression.descriptions import shared_description
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

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode
    from computer_use.protocol.records import Record
_PRESSABLE_FIELDS = (
    "pressable",
    "selectable",
    "focusable",
    "valueExpected",
    "incompleteLabelRelation",
    "labelTargets",
)


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
    matches_node = (
        True
        if node.get("_factBacked")
        and node["role"] == "cell"
        and target["role"] == "row"
        and state.member_of(node, target)
        else False
    )
    if matches_node:
        # Single-column item cells can contribute captions despite their own
        # coordinates/focus. compact_label separately preserves those fields
        # when deciding whether to fold the cell.
        # The control-label pass excludes table rows and multi-cell records.
        if any(node.get(key, {}).get("length", 1) != 1 for key in ("rowRange", "columnRange")):
            return False
        candidate = {key: value for key, value in node.items() if key not in ("rowRange", "columnRange", "index")}
        if candidate.get("titleRef") in state.nodes and state.member_of(state.nodes[candidate["titleRef"]], target):
            candidate.pop("titleRef")
        return layout(candidate, ignore_focus, ignore_selected)
    return layout(node, ignore_focus, ignore_selected)


def _owned_row_title(state: ProjectionView, node: ViewNode, target: ViewNode) -> bool:
    """A row cell may expose its own complete, unblocked descendant title."""
    return bool(
        (
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
        )
    )


def label_parts(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, target: ViewNode) -> list[Record] | None:
    """Label Parts.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
        target: Source/projected node in this exact view; its native identity is unchanged.
    """
    if _owned_row_title(state, node, target):
        title = state.nodes.get(node["titleRef"])
        matches_title = (
            True
            if (
                title is not None
                and not title.get("incompleteLabelRelation")
                and same_blocking(title, target)
                and len(state.targets.get(title["ref"], {})) == 1
                and node["ref"] in state.targets[title["ref"]]
                and utf16_length(title.get("value", "")) <= VALUE_PREVIEW_LIMIT
            )
            else False
        )
        if matches_title:
            return sources(title)
    if "capabilities" in target and not state.member_of(node, target):
        return None
    is_simple_text = (
        True
        if (
            node["role"] == "staticText"
            and not any(node.get(k) for k in _PRESSABLE_FIELDS)
            and not node["children"]
            and "partialReason" not in node
            and utf16_length(node.get("value", "")) <= VALUE_PREVIEW_LIMIT
        )
        else False
    )
    if is_simple_text:
        return sources(node)
    is_decorative_image = (
        True
        if (
            node["role"] == "image"
            and not any(node.get(k) for k in ("pressable", "selectable", "focusable", "actions", "actionsUnavailable"))
            and not node["children"]
            and "partialReason" not in node
        )
        else False
    )
    if is_decorative_image:
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
    declared_layout = any(
        m["ref"] == node["ref"] and m["part"] == "layout" for m in target.get("control", {}).get("members", [])
    )
    if (not declared_layout and label_parts(state, edit, node, target) is None) or not member_layout(
        state, edit, node, target, True, True
    ):
        return [node]
    if state.keep(node) or not same_blocking(node, target):
        return [node]
    repeated_selection = (
        node["role"] == "cell"
        and target["role"] == "row"
        and node.get("selected") is True
        and target.get("selected") is True
    )
    internal_focus = (
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
    if member_layout(state, edit, node, target, internal_focus, repeated_selection) and (
        node["ref"] != state.context.get("focusedRef") or internal_focus
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
    if member is None or "control" in node or state.keep(node):
        return [node]
    if not same_blocking(node, owner):
        return [node]
    edit.children(node, [c for child in node["children"] for c in compact_input(state, edit, child, owner)])
    owner_text = {owner.get("name"), owner.get("description"), owner.get("value")}
    if any(node.get(k) and node[k] not in owner_text for k in ("name", "description")) or (
        "value" in node and node["value"] != owner.get("value")
    ):
        return [node]
    if "value" in node and bool(node.get("valueIsNumber")) != bool(owner.get("valueIsNumber")):
        return [node]
    if node.get("valueExpected") and "value" not in node:
        return [node]
    if node.get("selectable") and not owner.get("selectable"):
        return [node]
    if (
        node.get("pressable")
        and not owner.get("pressable")
        and node.get("capabilities", {}).get("click") != owner.get("capabilities", {}).get("click")
    ):
        return [node]
    if (
        any(
            v == "available" and owner.get("capabilities", {}).get(k) != "available"
            for k, v in node.get("capabilities", {}).items()
        )
        or any(node.get(k) for k in ("actions", "actionsUnavailable", "help", "url"))
        or any(k in node and not shared_description(node, k) for k in ("valueDescription", "rangeDescription"))
        or any(node.get(k) for k in ("headingLevel", "landmarkType", "localizedLandmarkType", "fullDescription"))
    ):
        return [node]
    if (node.get("placeholder") and node["placeholder"] != owner.get("placeholder")) or has_incomplete_children(node):
        return [node]
    if any(node.get(k) for k in ("labelTargets", "incompleteLabelRelation", "preserveGroup", "subrole", "modal")) or (
        "selected" in node and node["selected"] != owner.get("selected")
    ):
        return [node]
    if (node.get("enabled") is False and owner.get("enabled") is not False) or node.get("expandable"):
        return [node]
    if node.get("submenu") or disclosure(node) is not None or "shortcut" in node:
        return [node]
    if "menuMark" in node or "toggleState" in node or any(key in node for key in TABLE_FIELDS):
        return [node]
    if node["ref"] == state.context.get("focusedRef") and node.get("focused") is False:
        return [node]
    if "selectionRange" in node or "selectedText" in node:
        if "value" not in owner or node.get("value") != owner["value"]:
            return [node]
        if "selectionRange" in owner and owner["selectionRange"] != node.get("selectionRange"):
            return [node]
        if "selectedText" in owner and owner["selectedText"] != node.get("selectedText"):
            return [node]
        if "selectionRange" in node:
            edit.set_field(owner, "selectionRange", node["selectionRange"], source=node)
        if "selectedText" in node:
            edit.set_field(owner, "selectedText", node["selectedText"], source=node)
    edit.fold(node, owner, "editor" if member["part"] == "editor" else "layout")
    return node["children"]
