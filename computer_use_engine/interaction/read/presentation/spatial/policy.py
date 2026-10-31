# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Semantic boundaries for geometry-assisted display organization."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.tree import has_incomplete_children, is_dialog
from computer_use.native.semantics import TABLE_FIELDS

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionView


REGIONS = frozenset(("group", "webArea", "document", "scrollArea", "window", "splitGroup"))
FIELDS = frozenset(("textField", "textArea", "comboBox", "popUpButton", "checkBox", "radioButton", "switch"))
CONTAINED_CAPTIONS = FIELDS | frozenset(("button", "menuButton", "link"))
LOGICAL_COLLECTIONS = frozenset(
    ("list", "outline", "table", "row", "cell", "column", "menu", "menuBar", "menuItem", "menuBarItem")
)


def form_control(node: Mapping[str, object], *, related: bool = False) -> bool:
    """Recognize value controls eligible for a source-backed form label.

    Args:
        node: Existing normalized fields and capabilities, left unchanged.
        related: A native label or description relation names this control.

    Returns:
        Whether a label can organize this control as a form unit. A selector's
        description remains a description; this does not infer its value or an
        input capability. Ordinary buttons and menu entries remain excluded.
    """
    role = node["role"]
    if role in FIELDS:
        return True
    if role not in ("menuButton", "incrementor", "slider"):
        return False
    scalar = any(key in node and node[key] is not None for key in ("value", "controlValue", "valueDescription"))
    description = node.get("description")
    described_choice = role == "menuButton" and isinstance(description, str) and bool(description.strip())
    return related or scalar or described_choice


def caption_target(node: Mapping[str, object]) -> bool:
    """Accept existing caption recipients and evidenced value-control variants."""
    return node["role"] in CONTAINED_CAPTIONS or form_control(node)


def logical_structure(node: NodeView) -> bool:
    """Recognize native sequence, hierarchy and table facts that govern reading order."""
    return (
        node["role"] in LOGICAL_COLLECTIONS
        or any(key in node for key in TABLE_FIELDS)
        or any(key in node for key in ("disclosureLevel", "disclosedBy", "incompleteDisclosureRelation"))
    )


def logical_source_scope(view: ProjectionView, node: NodeView) -> bool:
    """Retain the authoritative collection structure of every source ancestor."""
    ref: str | None = node["ref"]
    while ref is not None:
        if logical_structure(view.native_nodes[ref]):
            return True
        ref = view.parents.get(ref)
    return False


def available_region(node: NodeView) -> bool:
    """Allow local organization in complete regions without a logical row/column model."""
    return node["role"] in REGIONS and complete_visible(node) and not node.get("modal") and not is_dialog(node)


def complete_visible(node: NodeView) -> bool:
    """Require this local scope's known children and visual presence."""
    return not hidden(node) and not has_incomplete_children(node)


def hidden(node: NodeView) -> bool:
    """Keep explicitly hidden content outside visual association decisions."""
    return node.get("visible") is False or node.get("showing") is False or node.get("offscreen") is True


def visible_source_path(view: ProjectionView, node: NodeView) -> bool:
    """Respect explicit hidden ancestors retained in the original source graph."""
    ref: str | None = node["ref"]
    while ref is not None:
        if hidden(view.native_nodes[ref]):
            return False
        ref = view.parents.get(ref)
    return True


def source_region(view: ProjectionView, parent: NodeView, ref: str) -> tuple[str, ...] | None:
    """Identify multi-child source regions crossed by a projected child.

    Transparent wrappers can disappear during existing compression. Retain the
    distinction between their multi-child regions when later rules consider
    newly adjacent labels, controls or rows. Single-child wrappers add no
    partition. None marks an incomplete or hidden intervening scope.
    """
    if ref == parent["ref"]:
        return ()
    regions = []
    current = view.parents.get(ref)
    while current is not None and current != parent["ref"]:
        ancestor = view.native_nodes[current]
        if not complete_visible(ancestor):
            return None
        if len(ancestor["children"]) > 1:
            regions.append(current)
        current = view.parents.get(current)
    return tuple(regions) if current == parent["ref"] else None


def walk(roots: Sequence[NodeView]) -> Iterator[NodeView]:
    """Yield the current display forest in source-preserving depth-first order."""
    pending = list(reversed(roots))
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node["children"]))
