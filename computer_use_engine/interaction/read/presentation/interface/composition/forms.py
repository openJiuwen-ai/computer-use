# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize repeated label columns without changing control identity or ownership."""

from __future__ import annotations

from dataclasses import dataclass

from computer_use.interaction.read.presentation.interface.helpers import same_scope
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.regions.scope import (
    block_sources,
    ordered_ancestry,
    reading_scope,
)
from computer_use.interaction.read.presentation.spatial.association import referenceable_caption
from computer_use.interaction.read.presentation.spatial.policy import form_control
from computer_use.interaction.read.presentation.spatial.alignment import LayoutItem, analyze_alignment


@dataclass(frozen=True)
class FormBand:
    """A contiguous visual field with its original label and independently bound members."""

    label: str
    children: tuple[str, ...]


def form_bands(parent: InterfaceBlock, draft: InterfaceDraft) -> tuple[FormBand, ...]:
    """Find fields in a repeated label/content layout within one native parent.

    Require three aligned labels and two rows with value controls. Each band keeps
    the original sibling order, including multiple controls, captions and help.
    Native collections, missing bounds, scope changes and overlapping field rows
    end the local grouping. The result expresses visual grouping only.

    Args:
        parent: Existing unordered reading scope whose children are candidates.
        draft: Immutable source facts, comparable bounds and current block layout.

    Returns:
        Non-overlapping source-complete bands. Unclassified siblings remain in place.
    """
    if (
        parent.primary_ref is None
        or parent.layout != "tree"
        or draft.projected_nodes[parent.primary_ref]["role"]
        not in ("window", "sheet", "group", "splitGroup", "tabPanel", "scrollArea")
        or ordered_ancestry(parent.primary_ref, draft)
        or draft.evidence[parent.primary_ref].incomplete
        or draft.evidence[parent.primary_ref].hidden
    ):
        return ()
    blocks, nodes, boxes = draft.blocks, draft.projected_nodes, draft.source.geometry.boxes
    children = parent.children
    candidates: list[int] = []
    for index, key in enumerate(children[:-1]):
        block = blocks[key]
        ref = block.primary_ref
        if ref is None or block.children or ref not in boxes or not referenceable_caption(nodes[ref]):
            continue
        following = blocks[children[index + 1]].primary_ref
        if following is None or following not in boxes:
            continue
        label, content = boxes[ref], boxes[following]
        if (
            label.comparable(content)
            and 0 <= content.x - label.right <= 2 * label.height
            and label.y - 0.5 * label.height <= content.y <= label.bottom + label.height
        ):
            candidates.append(index)
    # A column is evidenced by repetition, not by choosing the nearest control.
    plan = draft.geometric_alignment.get(parent.key)
    alignment = plan.alignment if plan is not None else None
    if alignment is None and candidates:
        items = tuple(LayoutItem(children[index], boxes[blocks[children[index]].primary_ref]) for index in candidates)
        if len({item.box.space for item in items}) == 1:
            alignment = analyze_alignment(items)
    candidate_positions = {children[index]: index for index in candidates}
    columns: list[list[int]] = []
    claimed: set[int] = set()
    if alignment is not None:
        anchors = sorted(
            alignment.anchors,
            key=lambda anchor: (
                -len(set(anchor.members).intersection(candidate_positions)),
                ("left", "right", "center").index(anchor.kind),
                anchor.position,
            ),
        )
        for anchor in anchors:
            column = sorted(
                candidate_positions[key]
                for key in anchor.members
                if key in candidate_positions and candidate_positions[key] not in claimed
            )
            if len(column) >= 3:
                columns.append(column)
                claimed.update(column)
    result: list[FormBand] = []
    for column in columns:
        if len(column) < 3:
            continue
        proposed: list[FormBand] = []
        control_rows = 0
        for offset, start in enumerate(column):
            label_ref = blocks[children[start]].primary_ref
            assert label_ref is not None
            label = boxes[label_ref]
            stop = column[offset + 1] if offset + 1 < len(column) else len(children)
            next_box = boxes[blocks[children[stop]].primary_ref] if stop < len(children) else None
            if next_box is not None and next_box.y < label.bottom:
                continue
            refs = [label_ref]
            selected = [children[start]]
            bottom = label.bottom
            for key in children[start + 1 : stop]:
                block = blocks[key]
                ref = block.primary_ref
                if ref is None or ref not in boxes:
                    break
                box, evidence = boxes[ref], draft.evidence[ref]
                descendants = block_sources(key, blocks)
                if (
                    evidence.boundary
                    or evidence.incomplete
                    or evidence.hidden
                    or not same_scope(draft, (label_ref, ref))
                    or any(
                        draft.evidence[child].boundary
                        or draft.evidence[child].incomplete
                        or draft.evidence[child].hidden
                        or reading_scope(child, draft) != reading_scope(label_ref, draft)
                        for child in descendants
                    )
                    or reading_scope(ref, draft) != reading_scope(label_ref, draft)
                    or draft.source.parents.get(ref) != draft.source.parents.get(label_ref)
                    or not box.comparable(label)
                    or box.x < label.right
                    or box.y < label.y - 0.5 * label.height
                    or next_box is not None
                    and box.bottom > next_box.y
                    or box.y - bottom > 3 * label.height
                ):
                    break
                # A later native item jumping back up is a separate fixed surface.
                if box.bottom < bottom - label.height:
                    break
                refs.append(ref)
                selected.append(key)
                bottom = max(bottom, box.bottom)
            if len(selected) < 2:
                continue
            members = set(refs)
            related = [
                relation
                for relation in draft.relations
                if relation.kind in ("labelled_by", "labels", "described_by", "describes")
                and (relation.source in members or members.intersection(relation.targets))
            ]
            if any(
                not relation.complete
                or not relation.confirmed
                or relation.source not in members
                or not set(relation.targets) <= members
                for relation in related
            ):
                continue
            proposed.append(FormBand(label_ref, tuple(selected)))
            control_rows += any(form_control(nodes[ref]) for ref in refs[1:])
        if len(proposed) >= 3 and control_rows >= 2:
            result.extend(proposed)
    # Competing columns cannot claim any of the same original siblings.
    return tuple(
        band
        for band in result
        if not any(other is not band and set(band.children).intersection(other.children) for other in result)
    )
