# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Narrow immutable row-membership evidence for presentation-only table layout."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType

from computer_use.interaction.read.presentation.rendering.layout.model import RowCollection
from computer_use.protocol.observation import NativeObservation, Relation


def capture_row_collections(observation: NativeObservation, refs: Mapping[str, str]) -> Mapping[str, RowCollection]:
    """Retain complete outline rows and matching native child order.

    This evidence only permits a text layout; it adds no input precondition or
    capability. Missing, competing, sampled and failed relationships stay trees.
    """
    counts = Counter(refs.values())
    aliases = {token: ref for ref, token in refs.items() if counts[token] == 1}

    def complete(token: str, kind: str) -> Relation | None:
        node = observation.nodes.get(token)
        if node is None:
            return None
        choices = [(key, relation) for key, relation in node.relations.items() if relation.kind == kind]
        if len(choices) != 1:
            return None
        key, relation = choices[0]
        prefix = ("nodes", token, "relations", key)
        if (
            relation.evidence not in ("reported", "verified")
            or relation.enumeration != "enumerated"
            or relation.offset not in (None, 0)
            or relation.total != len(relation.targets)
            or relation.positions != tuple(range(len(relation.targets)))
            or len(set(relation.targets)) != len(relation.targets)
            or any(target not in observation.nodes or target not in aliases for target in relation.targets)
            or any(
                issue.path[: len(prefix)] == prefix or prefix[: len(issue.path)] == issue.path
                for issue in observation.issues
            )
        ):
            return None
        return relation

    result: dict[str, RowCollection] = {}
    for token, node in observation.nodes.items():
        if node.role != "tree" or token not in aliases:
            continue
        children, rows = complete(token, "children"), complete(token, "rows")
        if children is None or rows is None or not rows.targets:
            continue
        native_rows = tuple(target for target in children.targets if observation.nodes[target].role == "row")
        if native_rows != rows.targets:
            continue
        cells = {}
        for row in rows.targets:
            members = complete(row, "children")
            if members is not None:
                cells[aliases[row]] = tuple(aliases[target] for target in members.targets)
        result[aliases[token]] = RowCollection(
            tuple(aliases[row] for row in rows.targets),
            tuple(aliases[child] for child in children.targets),
            MappingProxyType(cells),
        )
    return MappingProxyType(result)
