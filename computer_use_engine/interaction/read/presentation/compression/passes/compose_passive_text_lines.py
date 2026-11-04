# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Present complete passive text fragments on one visual line as one text group."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.display_boundaries import display_boundary_reasons
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.compression.policy import same_blocking
from computer_use.interaction.read.presentation.ir import FactKey, SourceKey
from computer_use.interaction.read.presentation.tree import has_incomplete_children
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode
    from computer_use.interaction.read.presentation.spatial.geometry import Box
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def _caption(view: ProjectionView, node: ViewNode, evidence: CompressionEvidence) -> Record | None:
    """Find a complete whole caption without concatenating different native fields."""
    values = {node[field] for field in ("name", "description", "value") if type(node.get(field)) is str and node[field]}
    if len(values) != 1:
        return None
    (value,) = values
    if any(char in value for char in "\r\n\x85\u2028\u2029"):
        return None
    for field in ("name", "description", "value"):
        if node.get(field) != value or (node["ref"], field) not in evidence.complete_text:
            continue
        presented = view.field_value(node["ref"], field)
        fact = FactKey(SourceKey(node["ref"]), field)
        if (
            presented is not None
            and len(presented.parts) == 1
            and presented.parts[0].facts == (fact,)
            and presented.value == value
            and view.native_nodes[node["ref"]].get(field) == value
        ):
            return dict(sourceRef=node["ref"], attribute=field, field="name", text=value)
    return None


def _one_visual_line(owner: Box, boxes: list[Box]) -> bool:
    """Require one local reading band, allowing small outward glyph-bound rounding."""
    if any(not owner.comparable(box) for box in boxes):
        return False
    height = max(box.height for box in boxes)
    edge = 0.1 * min(box.height for box in boxes)
    if any(
        box.x < owner.x - edge
        or box.y < owner.y - edge
        or box.right > owner.right + edge
        or box.bottom > owner.bottom + edge
        for box in boxes
    ):
        return False
    overlap = min(box.bottom for box in boxes) - max(box.y for box in boxes)
    if overlap < 0.8 * height or owner.height > 1.5 * height:
        return False
    # AX text bounds can round neighboring glyph edges outwards. Keep this
    # tolerance relative to text height and narrow punctuation width, while
    # requiring the original reading direction and distinct fragment centers.
    return all(
        left.center[0] < right.center[0]
        and left.x <= right.x
        and -min(edge, 0.25 * min(left.width, right.width)) <= right.x - left.right <= height
        for left, right in zip(boxes, boxes[1:])
    )


def _help_only_owner_region(view: ProjectionView, owner: ViewNode) -> bool:
    """Keep the group's own Help beside its composed, passive child text.

    Help remains a field on the retained owner. Other structural or business
    region facts continue to require the owner's original child structure.
    """
    if owner["role"] != "group" or not owner.get("help"):
        return False
    if type(owner["help"]) is not str or owner["help"] != view.native_nodes[owner["ref"]].get("help"):
        return False
    return not display_boundary_reasons(view, owner, retained_help=True)


def compose_passive_text_lines(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Compose an unnamed group's complete static text line in original child order.

    Keep concrete controls, active state, incomplete content, semantic regions and
    independent columns as separate elements. Each unchanged string occurrence has
    its own source fact; the retained group gains a presentation caption only.
    The retained group can keep its own Help while its passive children form the
    line. Original roles, capabilities, references and exact fields remain in captured
    element details. No geometry is added to the model's text.

    Args:
        view: Read-only captured ownership, caption and comparable-bounds evidence.
        edit: Local display editor recording ordered caption parts and source folds.
    """
    evidence = view.context.get("compressionEvidence")
    if (
        not view.context.get("compact")
        or not view.context.get("nativeFacts")
        or view.context.get("subtreeRef")
        or not isinstance(evidence, CompressionEvidence)
    ):
        return
    pending = list(reversed(view.roots))
    while pending:
        owner = pending.pop()
        pending.extend(reversed(owner["children"]))
        children = tuple(owner["children"])
        if (
            owner["ref"] in view.folded_into
            or owner["role"] != "group"
            or not owner.get("_factBacked")
            or view.keep(owner)
            or len(children) < 2
            or (display_boundary_reasons(view, owner) and not _help_only_owner_region(view, owner))
            or has_incomplete_children(owner)
            or owner.get("layoutGroups")
            or owner.get("labelTargets")
            or owner.get("incompleteLabelRelation")
            or any(
                owner.get(field) not in (None, "")
                for field in ("name", "description", "value", "url", "controlValue", "rangeValue", "documentText")
            )
            or evidence.complete_children.get(owner["ref"]) != tuple(child["ref"] for child in children)
        ):
            continue
        parts: list[Record] = []
        boxes: list[Box] = []
        owner_box = view.geometry.boxes.get(owner["ref"])
        if owner_box is None:
            continue
        for child in children:
            box = view.geometry.boxes.get(child["ref"])
            part = _caption(view, child, evidence)
            if (
                child["role"] != "staticText"
                or child["children"]
                or view.keep(child)
                or display_boundary_reasons(view, child)
                or has_incomplete_children(child)
                or child.get("valueIsNumber")
                or child.get("labelTargets")
                or child.get("incompleteLabelRelation")
                or child.get("url")
                or any(field in child for field in ("documentText", "controlValue", "rangeValue"))
                or view.parents.get(child["ref"]) != owner["ref"]
                or view.scopes.get(child["ref"]) != view.scopes.get(owner["ref"])
                or not same_blocking(owner, child)
                or part is None
                or box is None
                or evidence.complete_children.get(child["ref"]) != ()
            ):
                break
            parts.append(part)
            boxes.append(box)
        else:
            if not _one_visual_line(owner_box, boxes):
                continue
            edit.adopt(owner, parts, "name", " ", preserve_occurrences=True)
            positions = []
            for child in children:
                edit.fold(child, owner, "passive-text-line")
                if child.get("spatialPosition"):
                    positions.append(dict(sourceRef=child["ref"], position=child["spatialPosition"]))
            edit.children(owner, ())
            edit.event(
                sourceRef=owner["ref"],
                kind="composed-passive-text-line",
                members=[child["ref"] for child in children],
                **(dict(internalPositions=positions) if positions else {}),
            )


def execute(run: CompressionRun, control: Control) -> CompressionRun:
    run.edit.run(compose_passive_text_lines, run.view)
    return run


def stage() -> Pass[CompressionRun, CompressionRun]:
    return Pass(
        "compose_passive_text_lines",
        execute,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=("complete local captions and containment", "comparable single-line bounds"),
            writes=("ordered source-backed caption", "passive text folds"),
            failures=("presentation_contract",),
        ),
    )
