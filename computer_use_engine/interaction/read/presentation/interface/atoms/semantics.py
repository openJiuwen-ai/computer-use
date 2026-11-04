# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Atomic role, value and state semantics shared by presentation planners."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.tree import (
    carries_value,
    disclosure,
    has_display_value,
    has_text_selection,
    is_dialog,
    is_hidden,
    selected_text_descendants,
)

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import NodeView


CONVENTIONAL = frozenset(
    {
        "button",
        "popUpButton",
        "menuButton",
        "checkBox",
        "radioButton",
        "switch",
        "link",
        "closeButton",
        "zoomButton",
        "minimizeButton",
        "textField",
        "textArea",
        "comboBox",
    }
)
STANDARD_CLICK = CONVENTIONAL | {"menuItem", "menuBarItem"}


@dataclass(frozen=True)
class RoleSelection:
    """Unescaped role spelling selected from exact native role evidence."""

    label: str
    attributes: tuple[str, ...]
    source_description: bool = False


@dataclass(frozen=True)
class StateSelection:
    """Ordered state/capability labels and their exact supporting field names."""

    labels: tuple[tuple[str, tuple[str, ...]], ...]
    equivalent_fields: tuple[str, ...] = ()
    selected_descendants: tuple[str, ...] = ()


def select_role(node: NodeView) -> RoleSelection:
    """Choose a role without escaping or joining it into an element line."""
    if is_dialog(node) and node["role"] != "sheet":
        return RoleSelection("dialog", ("role", "subrole"))
    if node.get("subrole") == "popover":
        return RoleSelection("popover", ("role", "subrole"))
    description = node.get("roleDescription")
    if node["role"] == "row" and (not description or description in ("row", "outline row", "外框行")):
        return RoleSelection("row", ("role", "roleDescription"))
    if node["role"] == "group" and (not description or description in ("group", "组")):
        return RoleSelection("container", ("role", "roleDescription"))
    if description:
        return RoleSelection(description, ("roleDescription",), True)
    return RoleSelection(re.sub(r"([a-z])([A-Z])", r"\1 \2", node["role"]).lower(), ("role",))


def value_is_displayed(node: NodeView, focused_ref: str | None) -> bool:
    """Determine whether the value has independent meaning in the control's caption."""
    native = node.get("native", node)
    return (
        carries_value(native)
        or node["ref"] == focused_ref
        and "value" in node
        or has_display_value(native)
        and node.get("value") != node.get("name")
        and node.get("value") != node.get("description")
    )


def displayed_value(node: NodeView) -> object:
    """Apply the established binary-value spelling while retaining the original in facts."""
    value = node.get("value")
    if node["role"] in ("checkBox", "switch", "radioButton") and (
        node.get("valueIsNumber") or value in ("true", "false")
    ):
        return {"0": "off", "1": "on", "true": "on", "false": "off"}.get(value, value)
    return value


def click_is_deferred(node: NodeView) -> bool:
    """Distinguish a provider's disabled report from a confirmed unavailable click."""
    caps = node.get("capabilities")
    return (
        node.get("enabled") is False
        and caps is not None
        and caps.get("click", "unknown") == "unknown"
        and not node.get("blockedBy")
    )


def select_states(node: NodeView, *, compact: bool, focused_ref: str | None) -> StateSelection:
    """Plan states using the existing capability and focus semantics.

    Every selected state retains the fields that justify it. Reported focus,
    selection, value and capability remain distinct facts.
    """
    labels: list[tuple[str, tuple[str, ...]]] = []
    equivalents: list[str] = []

    def add(label: str, *attributes: str) -> None:
        labels.append((label, attributes))

    if node.get("enabled") is False:
        add("reported disabled" if click_is_deferred(node) else "disabled", "enabled")
    if node.get("selected"):
        add("selected", "selected")
    if node.get("toggleState") in ("on", "off", "mixed"):
        shared = (
            compact
            and node["role"] in ("checkBox", "switch", "radioButton")
            and value_is_displayed(node, focused_ref)
            and displayed_value(node) == node["toggleState"]
        )
        if shared:
            equivalents.append("toggleState")
        else:
            add(node["toggleState"], "toggleState")
    if node.get("busy") is True:
        add("busy", "busy")
    for field in ("unsaved", "invalid"):
        if node.get(field) is True:
            add(field, field)
    read_only = tuple(field for field in ("readOnly", "textReadOnly", "rangeReadOnly") if field in node)
    if read_only and all(node[field] is True for field in read_only):
        add("read-only", *read_only)
    else:
        for field, label in (
            ("readOnly", "value read-only"),
            ("textReadOnly", "text read-only"),
            ("rangeReadOnly", "range read-only"),
        ):
            if node.get(field) is True:
                add(label, field)
    if node.get("headingLevel"):
        add("heading level " + str(node["headingLevel"]), "headingLevel")
    if node.get("landmarkType"):
        add(node["landmarkType"] + " landmark", "landmarkType")
    caps = node.get("capabilities")
    if caps is not None:
        if (
            caps.get("click") == "available"
            and not node.get("selectable")
            and (not compact or node["role"] not in STANDARD_CLICK)
        ):
            add("clickable", "capabilities")
        if (
            node.get("selectable")
            and node.get("enabled") is not False
            and not node.get("selected")
            and caps.get("click") != "unavailable"
        ):
            add("selectable", "selectable", "capabilities")
        if caps.get("typeText") == "available":
            add("text-input", "capabilities")
        elif caps.get("replaceText") == "available":
            add("replace-only", "capabilities")
        if (
            caps.get("replaceText", "unknown") != caps.get("typeText", "unknown")
            and caps.get("replaceText", "unknown") != "available"
        ):
            add("replace " + caps.get("replaceText", "unknown"), "capabilities")
        if (
            caps.get("focus") == "available"
            and caps.get("typeText") != "available"
            and node["role"] not in CONVENTIONAL
            and not node.get("selectable")
            and (not compact or node["role"] not in ("group", "webArea"))
        ):
            add("focusable", "capabilities")
    else:
        if node.get("selectable"):
            if not node.get("selected"):
                add("selectable", "selectable")
        elif node.get("pressable") and node["role"] not in CONVENTIONAL:
            add("clickable", "pressable")
        if (
            node.get("focusable")
            and node["role"] not in CONVENTIONAL
            and not node.get("selectable")
            and (not compact or node["role"] not in ("group", "webArea"))
        ):
            add("focusable", "focusable")
    if node.get("submenu"):
        add("submenu", "submenu")
    expanded = disclosure(node)
    if expanded is not None:
        add("expanded" if expanded else "collapsed", "expanded" if "expanded" in node else "disclosing")
        if "expanded" in node and node.get("disclosing") is expanded:
            equivalents.append("disclosing")
    if node["ref"] == focused_ref:
        add("focused")
    elif node.get("focused"):
        add("reports focus", "focused")
    if node.get("modal"):
        add("modal", "modal")
    elif node.get("modal") is False and is_dialog(node):
        add("non-modal", "modal")
    if is_hidden(node):
        add("hidden", "visible", "showing")
    descendants: tuple[str, ...] = ()
    if node["ref"] == focused_ref and not has_text_selection(node) and node["role"] not in ("application", "window"):
        descendants = tuple(item["ref"] for item in selected_text_descendants(node))
        if descendants:
            add("contains selected text")
    return StateSelection(tuple(labels), tuple(equivalents), descendants)
