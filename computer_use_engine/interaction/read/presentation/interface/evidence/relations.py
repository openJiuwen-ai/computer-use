# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain source-confirmed relations for local interface composition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.interface.model import InterfaceRelation

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import SnapshotFacts


def capture_relations(snapshot: SnapshotFacts) -> tuple[InterfaceRelation, ...]:
    """Map relation endpoints through the same snapshot's existing reference aliases.

    Missing endpoints and incomplete enumerations cannot justify composition. The
    original relation payload remains in snapshot facts for exact detail recovery.

    Args:
        snapshot: Acquired facts and the session-owned reference mapping.

    Returns:
        Ordered source relations with explicit completeness and certainty.
    """
    aliases = {token: ref for ref, token in snapshot.refs.items()}
    result = []
    for relation in snapshot.facts.get("relations", ()):
        source = aliases.get(relation["source"])
        if source is None:
            continue
        native_targets = relation["targets"]
        targets = tuple(aliases[token] for token in native_targets if token in aliases)
        offset = relation.get("offset", 0)
        positions = relation.get("positions", tuple(range(len(targets))))
        # An enumerated empty collection has no slots or page to locate. Its
        # explicit zero total establishes completeness when optional positions
        # and offset are absent. Nonempty collections still require their
        # existing ordering evidence.
        if not native_targets and relation.get("total") == 0:
            offset = 0 if offset is None else offset
            positions = () if positions is None else positions
        known_absent = (
            relation["state"] == "absent"
            and relation["certainty"] == "confirmed"
            and not native_targets
            and relation.get("total") == 0
        )
        complete = (
            (relation["state"] == "complete" or known_absent)
            and len(targets) == len(native_targets)
            and type(offset) is int
            and offset == 0
            and relation.get("total", len(targets)) == len(targets)
            and positions is not None
            and all(type(position) is int for position in positions)
            and tuple(positions) == tuple(range(len(targets)))
        )
        result.append(
            InterfaceRelation(
                source, relation["property"], relation["kind"], targets, complete, relation["certainty"] == "confirmed"
            )
        )
    return tuple(result)
