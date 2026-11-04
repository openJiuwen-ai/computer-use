# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Classify incidental inverse-label query errors on named native menu items."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import SnapshotFacts


def incidental_menu_label_error(snapshot: SnapshotFacts, relation: Mapping[str, Any]) -> bool:
    """Select an empty inverse-label failure whose original diagnostic stays in details.

    A complete menu title supplies this item's name independently of the query
    asking which other objects it labels. Own-title relations, known endpoints
    and collection/identity restrictions retain their public warning.
    """
    if (
        snapshot.native.backend != "macos"
        or relation["kind"] != "labels"
        or relation["state"] != "error"
        or relation["certainty"] != "unknown"
        or relation["targets"]
        or relation.get("cursor") is not None
    ):
        return False
    token, key = relation["source"], relation["recordKey"]
    if (
        snapshot.collection is None
        or snapshot.collection.relation_fields.get((token, key)) != "AXServesAsTitleForUIElements"
    ):
        return False
    node = snapshot.native.nodes.get(token)
    if node is None or node.role not in ("menu_item", "menu_bar_item") or type(node.name) is not str or not node.name:
        return False
    original = node.relations.get(key)
    if (
        original is None
        or original.kind != "labels"
        or original.targets
        or original.evidence != "candidate"
        or original.total is not None
        or original.offset is not None
        or original.positions is not None
        or original.enumeration != "enumerated"
    ):
        return False
    restrictions = []
    for issue in snapshot.native.issues:
        path = issue.path
        if len(path) < 2 or path[0] != "nodes":
            # Partial node membership leaves already acquired item properties
            # intact; the original collection warning has its own public route.
            if path != ("nodes",) or issue.state != "partial":
                return False
        elif path[1] == token:
            if len(path) == 2 or path[2] in ("role", "name") or path[2:] == ("relations",):
                return False
            if path[2:] == ("relations", key):
                restrictions.append(issue)
            elif path[2:4] == ("relations", key):
                return False
    return bool(restrictions) and all(
        issue.state == "error" and issue.continuation is None and not issue.alternatives for issue in restrictions
    )
