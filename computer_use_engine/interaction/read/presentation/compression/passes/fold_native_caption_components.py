# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Show a complete native control caption once instead of repeating its passive parts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.caption_equivalence import caption_equivalence
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView


def fold_native_caption_components(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Fold ordered inactive text leaves exactly covered by their native button/link caption.

    Preserve the owner's original field provenance and every native binding. Original
    component fields, states, actions and relationships remain available in captured details;
    independent controls, incomplete evidence and explicit subtree reads retain their tree.

    Args:
        view: Current projection and its immutable source facts/containment.
        edit: Local structural editor; native operation targets remain immutable.
    """
    if not view.context.get("compact") or view.context.get("subtreeRef") or not view.context.get("nativeFacts"):
        return
    evidence = view.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        return
    visible = {}
    pending = list(view.roots)
    while pending:
        node = pending.pop()
        visible[node["ref"]] = node
        pending.extend(node["children"])
    referenced_labels = frozenset(ref for targets in view.targets.values() for ref in targets)
    for owner in tuple(visible.values()):
        proof = caption_equivalence(view, owner, evidence, visible, referenced_labels)
        if proof is None:
            continue
        # Components must still be direct children in current display order.
        refs = [part.facts[0].source for part in proof.composition.parts]
        if [child["ref"] for child in owner["children"] if child["ref"] in refs] != refs:
            continue
        edit.fold_caption_components(proof)
        for ref in refs:
            visible.pop(ref, None)
