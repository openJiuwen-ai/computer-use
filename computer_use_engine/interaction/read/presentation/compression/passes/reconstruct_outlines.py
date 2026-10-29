# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reconstruct outlines."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import same_blocking

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode
"""Reconstruct outline hierarchy only from consistent disclosure relations."""


def outline(state: ProjectionView, edit: ProjectionEditor, node: ViewNode) -> None:
    """Outline.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    for child in node["children"]:
        outline(state, edit, child)
    if node["role"] != "outline":
        return
    stack, children = [], []
    rows = {c["ref"]: c for c in node["children"] if c["role"] == "row"}
    acyclic_results = {}

    def acyclic(row: ViewNode) -> bool:
        """Acyclic."""
        seen, valid, current = set(), True, row
        while current is not None:
            ref = current["ref"]
            if ref in acyclic_results:
                valid = acyclic_results[ref]
                break
            if ref in seen:
                valid = False
                break
            seen.add(ref)
            current = rows.get(current.get("disclosedBy", ""))
        for ref in seen:
            acyclic_results[ref] = valid
        return valid

    for row in node["children"]:
        parent_index = next(
            (i_value for i_value, parent_value in enumerate(stack) if parent_value["ref"] == row.get("disclosedBy")), -1
        )
        parent = stack[parent_index] if parent_index >= 0 else None
        compatible_level = (
            parent is None
            or "disclosureLevel" not in parent
            or "disclosureLevel" not in row
            or row["disclosureLevel"] == parent["disclosureLevel"] + 1
        )
        matches_compatible_level = (
            True
            if (
                row["role"] == "row"
                and parent is not None
                and acyclic(row)
                and not row.get("incompleteDisclosureRelation")
                and compatible_level
                and parent.get("expanded") is not False
                and parent.get("disclosing") is not False
                and same_blocking(row, parent)
                and parent.get("outlineDepth", 0) < 128
            )
            else False
        )
        if matches_compatible_level:
            edit.children(parent, (*parent["children"], row))
            edit.annotate(row, "outlineDepth", parent.get("outlineDepth", 0) + 1)
            edit.event(sourceRef=row["ref"], kind="outline", targetRef=parent["ref"])
            stack = stack[: parent_index + 1]
        else:
            children.append(row)
            stack = []
            if row["role"] == "row":
                edit.annotate(row, "outlineDepth", 0)
        if row["role"] == "row":
            stack.append(row)
    edit.children(node, children)


def reconstruct_outlines(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Rebuild display outline nesting from consistent native disclosure relationships.

    Visit the projection roots and nest eligible rows under their observed disclosure parent.
    Record outline depth and relationship changes while retaining inconsistent rows at their
    existing outline level.
    """
    for node in view.roots:
        outline(view, edit, node)
