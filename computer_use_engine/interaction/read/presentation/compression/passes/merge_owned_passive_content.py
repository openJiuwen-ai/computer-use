# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Present an atomic control and its covered passive content as one UI object."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.display_boundaries import display_boundary_reasons
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.compression.policy import ordinary_control, same_blocking
from computer_use.interaction.read.presentation.compression.value_lines import covered_value_lines
from computer_use.interaction.read.presentation.tree import has_incomplete_children

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode
    from computer_use.protocol.records import Record


_CONTENT_ROLES = frozenset(("group", "staticText", "image"))
_TEXT_FIELDS = ("name", "description", "value")


def _complete_path(view: ProjectionView, owner: ViewNode, node: ViewNode, evidence: CompressionEvidence) -> bool:
    """Confirm only the local original ownership path used by this candidate."""
    current, seen = node["ref"], set()
    while current not in seen:
        seen.add(current)
        original = view.native_nodes.get(current)
        if (
            original is None
            or view.scopes.get(current) != view.scopes.get(owner["ref"])
            or current in evidence.incomplete_children
            or has_incomplete_children(original)
            or current in evidence.contested_sources
        ):
            return False
        if current == owner["ref"]:
            return True
        parent = view.parents.get(current)
        if parent is None or current not in evidence.complete_children.get(parent, ()):
            return False
        current = parent
    return False


def _field_parts(
    view: ProjectionView, node: ViewNode, field: str, evidence: CompressionEvidence
) -> list[Record] | None:
    """Resolve one whole display string to complete, exact native source fields."""
    presented = view.field_value(node["ref"], field)
    if presented is None or not presented.parts:
        return None
    result = []
    for part in presented.parts:
        if type(part.value) is not str or not part.facts:
            return None
        for fact in part.facts:
            original = view.native_nodes.get(fact.source, {})
            if (
                (fact.source, fact.attribute) not in evidence.complete_text
                or fact.attribute not in _TEXT_FIELDS
                or type(original.get(fact.attribute)) is not str
                or original[fact.attribute] != part.value
            ):
                return None
            result.append(dict(sourceRef=fact.source, attribute=fact.attribute, field=field, text=part.value))
    return result


def _text_parts(view: ProjectionView, node: ViewNode, evidence: CompressionEvidence) -> list[Record] | None:
    """Return exact ordered caption sources, omitting only equal field aliases."""
    result, values = [], set()
    for field in _TEXT_FIELDS:
        value = node.get(field)
        if value in (None, ""):
            continue
        if type(value) is not str:
            return None
        if value in values:
            continue
        values.add(value)
        parts = _field_parts(view, node, field, evidence)
        if parts is None:
            return None
        result.extend(parts)
    return result


def _branch(
    view: ProjectionView, owner: ViewNode, root: ViewNode, evidence: CompressionEvidence
) -> tuple[list[ViewNode], list[Record]] | None:
    """Collect one wholly passive branch and its deepest readable caption sources."""
    nodes: list[ViewNode] = []

    def visit(node: ViewNode) -> list[Record] | None:
        if (
            node["role"] not in _CONTENT_ROLES
            or view.keep(node)
            or display_boundary_reasons(view, node)
            or not same_blocking(node, owner)
            or not _complete_path(view, owner, node, evidence)
            or has_incomplete_children(node)
            or node.get("valueIsNumber")
            or node.get("incompleteLabelRelation")
            or any(target != owner["ref"] for target in node.get("labelTargets", ()))
            or node.get("url")
            and node["role"] != "image"
            or any(field in node for field in ("documentText", "controlValue", "rangeValue"))
        ):
            return None
        own = _text_parts(view, node, evidence)
        if own is None:
            return None
        nodes.append(node)
        descendants: list[Record] = []
        for child in node["children"]:
            parts = visit(child)
            if parts is None:
                return None
            descendants.extend(parts)
        return descendants or own

    text = visit(root)
    return (nodes, text) if text is not None else None


def _captions(view: ProjectionView, owner: ViewNode, evidence: CompressionEvidence) -> tuple[set[str], set[str]]:
    """Read complete owner captions and the exact parts already represented by them."""
    whole, represented = set(), set()
    for field in _TEXT_FIELDS:
        if field == "value" and (owner.get("valueIsNumber") or owner["role"] in ("checkBox", "switch", "radioButton")):
            continue
        value = owner.get(field)
        if type(value) is not str or not value:
            continue
        presented = view.field_value(owner["ref"], field)
        if presented is None or _field_parts(view, owner, field, evidence) is None:
            continue
        whole.add(value)
        represented.add(value)
        represented.update(part.value for part in presented.parts)
    return whole, represented


def merge_owned_passive_content(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Fold passive images and covered text inside one retained atomic control.

    Require complete local ownership and preserve independent editors, actions,
    state and UI regions. A completely unnamed control may derive its caption
    from a wholly passive, complete, ordered text subtree. Covered positions
    become internal layout only when all child branches fold into the control.
    Original fields and operation references remain in their captured sources
    and element details.

    Args:
        view: Current display projection with immutable source facts and ownership.
        edit: Local presentation editor; native objects and input routes stay fixed.
    """
    if not view.context.get("compact") or not view.context.get("nativeFacts") or view.context.get("subtreeRef"):
        return
    evidence = view.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        return
    pending = list(reversed(view.roots))
    while pending:
        owner = pending.pop()
        pending.extend(reversed(owner["children"]))
        if (
            owner["ref"] in view.folded_into
            or not ordinary_control(owner)
            or not owner["children"]
            or owner["role"] in ("row", "cell")
            or has_incomplete_children(owner)
            or owner["ref"] in evidence.incomplete_content
        ):
            continue
        candidates = [(child, _branch(view, owner, child, evidence)) for child in owner["children"]]
        whole, represented = _captions(view, owner, evidence)
        text = [part for _, branch in candidates if branch is not None for part in branch[1]]
        # Use one occurrence per source fact, retaining duplicate values as a
        # boundary when a new name would otherwise collapse their multiplicity.
        ordered, seen = [], set()
        for part in text:
            key = (part["sourceRef"], part["attribute"])
            if key not in seen:
                seen.add(key)
                ordered.append(part)
        values = [part["text"] for part in ordered]
        if values and any(separator.join(values) in whole for separator in (" ", ", ", "")):
            represented.update(values)
        whole_represented = set(represented)
        value_lines = (
            covered_value_lines(view, owner, [part for part in ordered if part["text"] not in represented]) or ()
        )
        line_sources = {part["sourceRef"] for part in value_lines}
        represented.update(view.native_nodes[part["sourceRef"]][part["attribute"]] for part in value_lines)
        derived = False
        if not any(owner.get(field) not in (None, "") for field in _TEXT_FIELDS):
            if not values or len(values) != len(set(values)) or any(branch is None for _, branch in candidates):
                continue
            proposed = " ".join(values)
            if any(
                node.get(field) not in (None, "", proposed, *values)
                for _, branch in candidates
                for node in branch[0]
                for field in _TEXT_FIELDS
            ):
                continue
            edit.adopt(owner, ordered, "name", " ")
            whole, represented = _captions(view, owner, evidence)
            whole_represented = set(represented)
            derived = True
        if not whole:
            continue
        mergeable = [
            branch is not None
            and all(
                node.get(field) in (None, "", *whole_represented)
                or node["ref"] in line_sources
                and node.get(field) in represented
                for node in branch[0]
                for field in _TEXT_FIELDS
            )
            for _, branch in candidates
        ]
        # Positions describe internal label layout only when the complete control
        # becomes one object. A retained branch makes positioned peers real regions.
        complete_control = all(mergeable)
        folded, retained, positions = [], [], []
        for (child, branch), covered in zip(candidates, mergeable):
            if (
                branch is None
                or not covered
                or not complete_control
                and any(node.get("spatialPosition") for node in branch[0])
            ):
                retained.append(child)
                continue
            for node in branch[0]:
                edit.fold(node, owner, "owned-content")
                folded.append(node["ref"])
                if node.get("spatialPosition"):
                    positions.append(dict(sourceRef=node["ref"], position=node["spatialPosition"]))
        if folded:
            edit.children(owner, retained)
            covered_lines = [part for part in value_lines if part["sourceRef"] in folded]
            edit.event(
                sourceRef=owner["ref"],
                kind="owned-passive-content",
                members=folded,
                derivedCaption=derived,
                **(dict(valueLines=covered_lines) if covered_lines else {}),
                **(dict(internalPositions=positions) if not retained and positions else {}),
            )
