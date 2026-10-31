# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared guards for lossless presentation, not native capability discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.descriptions import shared_description
from computer_use.interaction.read.presentation.policy import independent_actions
from computer_use.interaction.read.presentation.tree import (
    carries_value,
    disclosure,
    has_display_value,
    has_incomplete_children,
    is_dialog,
    unique,
)
from computer_use.native.semantics import TABLE_FIELDS

if TYPE_CHECKING:
    from typing import Any

    from computer_use.interaction.read.presentation.records import NodeView


WRAPPERS = {"group", "cell", "column"}
REGIONS = {
    "window",
    "sheet",
    "group",
    "splitGroup",
    "scrollArea",
    "toolbar",
    "tabGroup",
    "outline",
    "list",
    "table",
    "webArea",
    "document",
}
CONTROLS = {
    "button",
    "checkBox",
    "radioButton",
    "switch",
    "textField",
    "textArea",
    "comboBox",
    "popUpButton",
    "menuButton",
    "link",
    "row",
}


def sources(node: NodeView) -> list[Any]:
    """Sources.

    Args:
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    result = []
    for attribute in ("name", "description", "value"):
        if not node.get(attribute):
            continue
        parts = [part for part in node["textSources"] if part["field"] == attribute]
        if attribute == "value" and not parts:
            result.append(dict(sourceRef=node["ref"], attribute=attribute, field="name", text=node["value"]))
        else:
            result.extend(parts)
    return result


def content(node: NodeView) -> list[Any]:
    return unique(part["text"] for part in sources(node))


def represented(source: NodeView, target: NodeView) -> bool:
    texts = {part["text"] for part in target["textSources"]}
    return all(text in texts for text in content(source))


def protected_content(node: NodeView) -> bool:
    """Protected Content.

    Args:
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    return bool(
        "selectionRange" in node
        or node.get("selectedText") is not None
        or node.get("actionsUnavailable")
        or node.get("placeholder")
        or "documentUrl" in node
        or "documentReports" in node
        or any(
            field in node and node[field] is not False
            for field in ("readOnly", "textReadOnly", "rangeReadOnly", "unsaved", "invalid", "errorRelations")
        )
        or any(
            field in node and not shared_description(node, field) for field in ("valueDescription", "rangeDescription")
        )
        or any(node.get(field) for field in ("documentText", "controlValue", "rangeValue"))
        or any(
            node.get(field) for field in ("headingLevel", "landmarkType", "localizedLandmarkType", "fullDescription")
        )
        or (independent_actions(node) if node.get("_factBacked") else node.get("actions"))
        or node.get("busy") is True
        or has_incomplete_children(node)
        or "shortcut" in node
        or "menuMark" in node
        or "toggleState" in node
        or "disclosureLevel" in node
        or node.get("disclosedBy")
        or node.get("incompleteDisclosureRelation")
        or any(key in node for key in TABLE_FIELDS)
    )


def independent(node: NodeView) -> bool:
    """Independent.

    Args:
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    capabilities = node.get("capabilities", {})
    available = any(
        value == "available"
        for key, value in capabilities.items()
        if not (node.get("_factBacked") and node["role"] in WRAPPERS and key == "focus")
    )
    return bool(
        available
        or (node.get("focusable") and node["role"] not in WRAPPERS)
        or node.get("labelTargets")
        or node.get("incompleteLabelRelation")
        or node.get("preserveGroup")
        or (node.get("subrole") and node["subrole"] != "hostingView")
        or "control" in node
        or node.get("titleRef")
        or carries_value(node)
        or node.get("help")
        or node.get("url")
        or (
            node.get("identifier")
            and not node.get("name")
            and not node.get("description")
            and not node.get("value")
            and not node["children"]
        )
        or protected_content(node)
        or is_dialog(node)
        or node.get("modal")
    )


def same_blocking(a: NodeView, b: NodeView) -> bool:
    return ",".join(a.get("blockedBy", [])) == ",".join(b.get("blockedBy", []))


def layout(node: NodeView, ignore_focus: bool = False, ignore_selected: bool = False) -> bool:
    return (
        node["role"] in WRAPPERS
        and not node.get("name")
        and not node.get("description")
        and not has_display_value(node)
        and not independent(node)
        and not node.get("pressable")
        and not node.get("selectable")
        and node.get("enabled") is not False
        and (ignore_selected or not node.get("selected"))
        and (ignore_focus or not node.get("focused"))
        and not node.get("expandable")
        and not node.get("submenu")
        and disclosure(node) is None
    )


def ordinary_control(node: NodeView) -> bool:
    return (
        node["role"] in CONTROLS
        or bool(node.get("pressable"))
        or bool(node.get("_factBacked") and node.get("capabilities", {}).get("click") == "available")
    )


def secondary_actions(node: NodeView) -> list[Any]:
    """Secondary Actions.

    Args:
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    result = []
    for action in node.get("actions", []):
        kind, caps = action.get("kind", action["name"]), node.get("capabilities")
        if (
            kind == "invoke"
            and not node.get("selectable")
            and (
                caps.get("click") == "available"
                if caps is not None
                else ordinary_control(node) or node["role"] in ("menuItem", "menuBarItem")
            )
        ):
            continue
        matches_kind = (
            True
            if (
                kind == "select"
                and not node.get("selectable")
                and caps is None
                and node["role"] in ("menuItem", "menuBarItem")
            )
            else False
        )
        if matches_kind:
            continue
        matches_kind_condition = (
            True
            if (
                kind == "show_menu"
                and not node.get("selectable")
                and node.get("pressable")
                and node["role"] in ("popUpButton", "menuButton", "comboBox")
                and (
                    caps is None
                    or (
                        caps.get("click") == "available"
                        and not any(a.get("kind", a["name"]) == "invoke" for a in node.get("actions", []))
                    )
                )
            )
            else False
        )
        if matches_kind_condition:
            continue
        if kind in ("expand", "collapse") and node.get("expandable") and caps is None:
            continue
        result.append(action)
    return result
