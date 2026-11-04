# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local UI boundaries for compact structural folds, separate from native abilities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.tree import disclosure, is_dialog
from computer_use.native.semantics import TABLE_FIELDS

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionView, ViewNode


_INPUT_ROLES = frozenset(("textField", "textArea", "comboBox", "document"))
_REGION_ROLES = frozenset(
    (
        "application",
        "window",
        "sheet",
        "popover",
        "webArea",
        "document",
        "scrollArea",
        "splitGroup",
        "toolbar",
        "tabGroup",
        "tabPanel",
        "list",
        "outline",
        "table",
        "row",
        "cell",
        "column",
        "menu",
        "menuBar",
        "heading",
    )
)
_DETAIL_ACTIONS = frozenset(("show_menu", "scroll_into_view"))


def empty_content(value: object) -> bool:
    """Recognize absent or empty strings while retaining numeric and boolean values."""
    return value is None or type(value) is str and value == ""


def _neutral_selection(value: object) -> bool:
    """Accept only the exact inactive zero-length selection used by display policy."""
    return (
        isinstance(value, Mapping)
        and set(value) == {"location", "length"}
        and type(value["location"]) is int
        and value["location"] == 0
        and type(value["length"]) is int
        and value["length"] == 0
    )


def display_boundary_reasons(view: ProjectionView, node: ViewNode, *, retained_help: bool = False) -> frozenset[str]:
    """Identify UI facts that require this source to remain a distinct display object.

    Concrete editors, business actions, meaningful state and structural scopes remain
    boundaries. Generic native setters and SDK route uncertainty remain exact captured
    details; their presence does not create an editor or a new execution route.

    Args:
        view: Current projection with frozen source identities and local display evidence.
        node: Original object considered for a structural fold or owned-content merge.
        retained_help: The caller keeps this owner and its original Help field visible.
            Exclude Help alone from the owner's region reasons for that query.

    Returns:
        Applicable input, action, state, region, scope and incomplete-content categories.
    """
    reasons: set[str] = set()
    evidence = view.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        # These rules require real source evidence, rather than dictionary shape alone.
        return frozenset(("scope",))
    ref = node["ref"]
    if (
        node["role"] in _INPUT_ROLES
        or ref in evidence.concrete_inputs
        or node.get("control", {}).get("kind") == "textInput"
        or node.get("editorOwnerRef")
    ):
        reasons.add("input")
    if (
        node.get("actionsUnavailable")
        or node.get("pressable")
        or node.get("selectable")
        or node.get("_factBacked")
        and node.get("capabilities", {}).get("click") == "available"
        or any(action.get("kind", action.get("name")) not in _DETAIL_ACTIONS for action in node.get("actions", ()))
        or node.get("shortcut")
        or node.get("submenu")
    ):
        reasons.add("action")
    if (
        ref == view.context.get("focusedRef")
        or node.get("focused") is True
        or node.get("selected") is True
        or node.get("focusable") is True
        or node.get("enabled") is False
        or node.get("visible") is False
        or node.get("showing") is False
        or node.get("offscreen") is True
        or node.get("busy") is True
        or node.get("modal") is True
        or any(
            field in node and node[field] is not False
            for field in ("readOnly", "textReadOnly", "rangeReadOnly", "unsaved", "invalid", "errorRelations")
        )
        or not empty_content(node.get("selectedText"))
        or "selectionRange" in node
        and not _neutral_selection(node["selectionRange"])
        or "toggleState" in node
        or node.get("expandable")
        or disclosure(node) is not None
        or node.get("menuMark")
    ):
        reasons.add("state")
    if (
        node["role"] in _REGION_ROLES
        or ref in evidence.semantic_regions
        or is_dialog(node)
        or any(field in node for field in TABLE_FIELDS)
        or not retained_help
        and not empty_content(node.get("help"))
        or any(
            not empty_content(node.get(field))
            for field in (
                "placeholder",
                "fullDescription",
                "landmarkType",
                "localizedLandmarkType",
                "valueDescription",
                "rangeDescription",
            )
        )
        or "headingLevel" in node
        or "documentUrl" in node
        or "documentReports" in node
        or "disclosureLevel" in node
        and node["disclosureLevel"] != 0
        or node.get("disclosedBy")
        or node.get("incompleteDisclosureRelation")
        or node.get("subrole") not in (None, "", "hostingView")
    ):
        reasons.add("region")
    if ref in evidence.contested_sources:
        reasons.add("scope")
    if ref in evidence.incomplete_content:
        reasons.add("content")
    return frozenset(reasons)
