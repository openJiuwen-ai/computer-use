# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fold unambiguous visual captions or link their retained independent nodes."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.captions import passive_caption
from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.compression.policy import represented, sources, same_blocking
from computer_use.interaction.read.presentation.compression.value_lines import covered_value_lines
from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView
from computer_use.interaction.read.presentation.spatial.association import (
    caption_competitor,
    referenceable_caption,
    unique_matches,
)
from computer_use.interaction.read.presentation.spatial.geometry import GeometryIndex
from computer_use.interaction.read.presentation.spatial.policy import (
    available_region,
    caption_target,
    complete_visible,
    hidden,
    source_region,
    visible_source_path,
    walk,
)
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def associate_spatial_labels(view: ProjectionView, edit: ProjectionEditor, geometry: GeometryIndex) -> None:
    """Fold a unique passive caption, or retain its fields through a visual reference.

    Protected metadata keeps the label independent. A reference makes its visual
    relationship explicit while preserving the target's own name and value.
    """
    for parent in walk(view.roots):
        if not visible_source_path(view, parent) or not (
            available_region(parent) or caption_target(parent) and complete_visible(parent)
        ):
            continue
        children = list(parent["children"])
        candidates = [child for child in children if caption_competitor(child)]
        if caption_competitor(parent):
            candidates.append(parent)
        # Named and incomplete controls still compete for nearby captions.
        # Eligibility to receive a new name is checked only after matching.
        targets = [node for node in candidates if not hidden(node)]
        labels = [
            node
            for node in children
            if node["role"] == "staticText"
            and not node["children"]
            and not hidden(node)
            and not node.get("labelTargets")
            and not view.targets.get(node["ref"])
        ]
        consumed = set()
        for pair in unique_matches(labels, targets, geometry):
            label, target = view.nodes[pair.label], view.nodes[pair.target]
            label_region = source_region(view, parent, pair.label)
            if (
                not caption_target(target)
                or not complete_visible(target)
                or not complete_visible(label)
                or any(target.get(key) for key in ("titleRef", "linkedLabel", "incompleteLabelRelation"))
                or label_region is None
                or label_region != source_region(view, parent, pair.target)
                or view.scopes[pair.label] != view.scopes[pair.target]
                or not same_blocking(label, target)
            ):
                continue
            # An input's own value descendant is content, rather than an external
            # caption. Keep native label relationships and independent siblings intact.
            if covered_value_lines(view, target, sources(label)) is not None:
                continue
            if (
                not view.context.get("deferVisualComposition")
                and not any(target.get(key) for key in ("name", "description", "preserveGroup"))
                and passive_caption(view, edit, label, target)
            ):
                edit.adopt(target, sources(label), "name", " ")
                edit.fold(label, target, "label")
                edit.event(
                    sourceRef=pair.label,
                    targetRef=pair.target,
                    kind="spatial-label",
                    relation=pair.relation,
                    geometrySources=(pair.label, pair.target),
                )
                consumed.add(pair.label)
            elif referenceable_caption(label) and not represented(label, target):
                edit.annotate(target, "visualLabelRef", pair.label)
                edit.annotate(
                    target, "_contextRefs", tuple(dict.fromkeys((*target.get("_contextRefs", ()), pair.label)))
                )
                edit.event(
                    sourceRef=pair.label,
                    targetRef=pair.target,
                    kind="spatial-label-reference",
                    relation=pair.relation,
                    geometrySources=(pair.label, pair.target),
                )
        if consumed:
            edit.children(parent, [node for node in children if node["ref"] not in consumed])


def execute(run: CompressionRun, control: Control) -> CompressionRun:
    run.edit.run(associate_spatial_labels, run.view, run.ir.source.geometry)
    return run


def stage() -> Pass[CompressionRun, CompressionRun]:
    return Pass(
        "associate_spatial_labels",
        execute,
        Contract(
            CompressionRun,
            CompressionRun,
            {Effect.PURE},
            requires=("geometry", "caption facts", "native scope and labels"),
            writes=("caption composition", "label elisions", "visual label reference", "diff context"),
            failures=("presentation_contract",),
        ),
    )
