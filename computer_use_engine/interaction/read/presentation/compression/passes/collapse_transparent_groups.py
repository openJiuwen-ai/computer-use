# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Splice one-child implementation groups while retaining their original facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.display_boundaries import (
    display_boundary_reasons,
    empty_content,
)
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.compression.policy import same_blocking
from computer_use.interaction.read.presentation.tree import has_incomplete_children

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def _complete_caption(view: ProjectionView, node: ViewNode, field: str, evidence: CompressionEvidence) -> str | None:
    """Read a whole displayed string backed by complete, exact source fields."""
    value = view.field_value(node["ref"], field)
    if value is None or not value.parts or type(value.value) is not str or not value.value:
        return None
    for part in value.parts:
        if type(part.value) is not str or not part.facts:
            return None
        for fact in part.facts:
            original = view.native_nodes.get(fact.source, {})
            if (
                fact.attribute not in ("name", "description", "value")
                or (fact.source, fact.attribute) not in evidence.complete_text
                or type(original.get(fact.attribute)) is not str
                or original[fact.attribute] != part.value
            ):
                return None
    return value.value


def _covered_caption_group(
    view: ProjectionView, node: ViewNode, child: ViewNode, evidence: CompressionEvidence
) -> bool:
    """Recognize one passive label wrapper exactly covering its sole real control."""
    if (
        not view.context.get("nativeFacts")
        or node.get("linkedLabel")
        or node.get("titleRef")
        or node.get("incompleteLabelRelation")
        or any(node["ref"] in targets for targets in view.targets.values())
        or child["ref"] in evidence.incomplete_content
    ):
        return False
    bounds = view.geometry.boxes
    if node["ref"] not in bounds or bounds.get(child["ref"]) != bounds[node["ref"]]:
        return False
    # Named wrappers need uncontested content ownership, including any anonymous
    # one-child layers already removed by the same bottom-up traversal.
    current = node["ref"]
    while True:
        if current in evidence.contested_sources:
            return False
        if current == child["ref"]:
            break
        current = evidence.complete_children[current][0]
    captions = {
        caption
        for field in ("name", "description", "value")
        if (caption := _complete_caption(view, child, field, evidence)) is not None
    }
    original = view.native_nodes[node["ref"]]
    nonempty = False
    for field in ("name", "description"):
        value = original.get(field)
        if type(value) is not str or (node["ref"], field) not in evidence.complete_text:
            return False
        if value:
            nonempty = True
            if value not in captions:
                return False
        if not empty_content(node.get(field)) and _complete_caption(view, node, field, evidence) not in captions:
            return False
    return nonempty


def _single_source_path(view: ProjectionView, evidence: CompressionEvidence, parent: ViewNode, child: ViewNode) -> bool:
    """Confirm the local original single-child chain after lower wrappers were spliced."""
    current = parent["ref"]
    target = child["ref"]
    visited: set[str] = set()
    while current != target:
        if current in visited or current in evidence.splice_conflicts:
            return False
        visited.add(current)
        children = evidence.complete_children.get(current, ())
        if len(children) != 1:
            return False
        following = children[0]
        if view.parents.get(following) != current or view.scopes.get(following) != view.scopes.get(parent["ref"]):
            return False
        current = following
    # The terminal node remains displayed. Its outgoing conflicts belong to its
    # own subtree, while conflicts on this incoming edge also mark its parent.
    return True


def collapse_transparent_groups(
    view: ProjectionView,
    edit: ProjectionEditor,
    node: ViewNode,
    scope: Scope,
) -> list[ViewNode] | tuple[ViewNode, ...]:
    """Replace a passive implementation group with its sole captured child.

    Anonymous groups keep their existing structural checks. A named group also
    needs complete captions exactly repeated by the retained child and identical
    comparison bounds. Business content, input, state and structural boundaries
    remain distinct; original fields and operation references stay in details.

    Args:
        view: Current projection, including immutable local containment evidence.
        edit: Presentation editor recording the splice without rebinding either object.
        node: Candidate group after its children have completed local compression.
        scope: Parent and table context owned by the traversal.

    Returns:
        The original node, or its single child for the parent traversal to splice in.
    """
    evidence = view.context.get("compressionEvidence")
    if (
        not view.context.get("compact")
        or view.context.get("subtreeRef")
        or not node.get("_factBacked")
        or node["role"] != "group"
        or scope.in_table
        or view.keep(node)
        or len(node["children"]) != 1
        or not isinstance(evidence, CompressionEvidence)
        or has_incomplete_children(node)
        or node.get("spatialPosition")
        or node.get("labelTargets")
        or any(
            not empty_content(node.get(field))
            for field in ("value", "controlValue", "documentText", "rangeValue", "url")
        )
        or node.get("roleDescription") not in (None, "", "group", "组", "container")
    ):
        return [node]
    boundaries = display_boundary_reasons(view, node)
    if node["ref"] not in evidence.splice_conflicts:
        boundaries -= {"scope"}
    if boundaries:
        return [node]
    child = node["children"][0]
    if not same_blocking(node, child) or not _single_source_path(view, evidence, node, child):
        return [node]
    named = any(not empty_content(node.get(field)) for field in ("name", "description"))
    if named and not _covered_caption_group(view, node, child, evidence):
        return [node]
    edit.elide(
        node, "splice", (child,), event_kind="covered-caption-group" if named else "transparent-group", target=child
    )
    return (child,)
