# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep a shared derived row caption on its sole retained cell."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.tree import has_incomplete_children

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def _caption_sources(view: ProjectionView, node: ViewNode) -> frozenset[tuple[str, str, str]]:
    """Return exact source fields for one complete, uncombined name."""
    name = node.get("name")
    value = view.field_value(node["ref"], "name")
    if not isinstance(name, str) or not name or value is None or len(value.parts) != 1 or value.parts[0].value != name:
        return frozenset()
    return frozenset((fact.source, fact.attribute, name) for fact in value.parts[0].facts)


def deduplicate_row_captions(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Display a repeated derived row name once on its sole independent cell.

    Require the row and cell to retain their original direct containment and identical,
    complete caption sources within one scope. Keep both objects, their native names,
    actions and other fields; record the cell as the remaining caption destination.
    """
    if not view.context.get("compact") or view.context.get("subtreeRef"):
        return
    evidence = view.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        return
    pending = list(view.roots)
    while pending:
        row = pending.pop()
        pending.extend(row["children"])
        original = view.native_nodes[row["ref"]]
        if (
            row["role"] != "row"
            or original.get("name") is not None
            or len(row["children"]) != 1
            or len(original["children"]) != 1
            or has_incomplete_children(row)
            or has_incomplete_children(original)
        ):
            continue
        cell = row["children"][0]
        if (
            cell["role"] != "cell"
            or original["children"][0]["ref"] != cell["ref"]
            or view.parents.get(cell["ref"]) != row["ref"]
            or view.scopes[row["ref"]] != view.scopes[cell["ref"]]
            or has_incomplete_children(cell)
            or row.get("name") != cell.get("name")
        ):
            continue
        sources = _caption_sources(view, row)
        if (
            not sources
            or sources != _caption_sources(view, cell)
            or any(ref == row["ref"] or view.scopes.get(ref) != view.scopes[row["ref"]] for ref, _, _ in sources)
            or any((ref, attribute) not in evidence.complete_text for ref, attribute, _ in sources)
        ):
            continue
        edit.suppress(row, "name")
        edit.event(
            sourceRef=row["ref"],
            kind="repeatedDerivedName",
            targetRef=cell["ref"],
            field="name",
            sources=[
                dict(sourceRef=ref, attribute=attribute, value=value) for ref, attribute, value in sorted(sources)
            ],
        )
