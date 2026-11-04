# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share proven empty descriptions without changing their independent source targets."""

from __future__ import annotations

from collections import defaultdict

from computer_use.interaction.read.presentation.compression.descriptions import (
    DESCRIPTION_LABELS,
    description_declaration,
)
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.ir import (
    FactKey,
    ProjectionEditor,
    ProjectionView,
    SharedDescription,
    SourceKey,
)


def share_empty_descriptions(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Declare repeated complete empty fields once per original window or menu scope.

    Only compact whole-view output participates. Each source retains its native value,
    identity and unrelated preservation rules; the shared declaration accounts solely
    for the otherwise repeated description field.
    """
    evidence = view.context.get("compressionEvidence")
    if (
        not view.context.get("compact")
        or not view.context.get("shareDescriptions", True)
        or view.context.get("subtreeRef")
        or not isinstance(evidence, CompressionEvidence)
    ):
        return
    groups: dict[tuple[str, str], list[FactKey]] = defaultdict(list)
    for ref in view.order:
        node = view.nodes[ref]
        scope = view.scopes[ref]
        if view.native_nodes[scope]["role"] not in ("window", "sheet", "menuBar"):
            continue
        for attribute in DESCRIPTION_LABELS:
            if (
                (ref, attribute) in evidence.complete_descriptions
                and type(node.get(attribute)) is str
                and node[attribute] == ""
            ):
                groups[scope, attribute].append(FactKey(SourceKey(ref), attribute))
    for (scope, attribute), facts in groups.items():
        if len(facts) < 2:
            continue
        group = SharedDescription(SourceKey(scope), attribute, "", tuple(facts))
        repeated_length = len(facts) * len(DESCRIPTION_LABELS[attribute] + ': ""')
        if len(description_declaration(group)) < repeated_length:
            edit.share_description(group)
