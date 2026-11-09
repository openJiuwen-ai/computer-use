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
    # pylint: disable-next=unidiomatic-typecheck
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


def _input_boundary(node: ViewNode, evidence: CompressionEvidence, ref: str) -> bool:
    if node["role"] in _INPUT_ROLES or ref in evidence.concrete_inputs:
        return True
    return bool(node.get("control", {}).get("kind") == "textInput" or node.get("editorOwnerRef"))


def _action_boundary(node: ViewNode) -> bool:
    if any(node.get(key) for key in ("actionsUnavailable", "pressable", "selectable")):
        return True
    if node.get("_factBacked") and node.get("capabilities", {}).get("click") == "available":
        return True
    if any(action.get("kind", action.get("name")) not in _DETAIL_ACTIONS for action in node.get("actions", ())):
        return True
    return bool(node.get("shortcut") or node.get("submenu"))


def _state_boundary(view: ProjectionView, node: ViewNode, ref: str) -> bool:
    if ref == view.context.get("focusedRef"):
        return True
    if any(node.get(field) is True for field in ("focused", "selected", "focusable")):
        return True
    if any(node.get(field) is False for field in ("enabled", "visible", "showing")):
        return True
    if any(node.get(field) is True for field in ("offscreen", "busy", "modal")):
        return True
    if any(
        field in node and node[field] is not False
        for field in ("readOnly", "textReadOnly", "rangeReadOnly", "unsaved", "invalid", "errorRelations")
    ):
        return True
    if not empty_content(node.get("selectedText")):
        return True
    if "selectionRange" in node and not _neutral_selection(node["selectionRange"]):
        return True
    return bool("toggleState" in node or node.get("expandable") or disclosure(node) is not None or node.get("menuMark"))


def _region_boundary(node: ViewNode, evidence: CompressionEvidence, ref: str, retained_help: bool) -> bool:
    if node["role"] in _REGION_ROLES or ref in evidence.semantic_regions or is_dialog(node):
        return True
    if any(field in node for field in TABLE_FIELDS) or (not retained_help and not empty_content(node.get("help"))):
        return True
    descriptive_fields = (
        "placeholder",
        "fullDescription",
        "landmarkType",
        "localizedLandmarkType",
        "valueDescription",
        "rangeDescription",
    )
    if any(not empty_content(node.get(field)) for field in descriptive_fields):
        return True
    if any(field in node for field in ("headingLevel", "documentUrl", "documentReports")):
        return True
    if "disclosureLevel" in node and node["disclosureLevel"] != 0:
        return True
    return bool(
        node.get("disclosedBy")
        or node.get("incompleteDisclosureRelation")
        or node.get("subrole") not in (None, "", "hostingView")
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
    if _input_boundary(node, evidence, ref):
        reasons.add("input")
    if _action_boundary(node):
        reasons.add("action")
    if _state_boundary(view, node, ref):
        reasons.add("state")
    if _region_boundary(node, evidence, ref, retained_help):
        reasons.add("region")
    if ref in evidence.contested_sources:
        reasons.add("scope")
    if ref in evidence.incomplete_content:
        reasons.add("content")
    return frozenset(reasons)
