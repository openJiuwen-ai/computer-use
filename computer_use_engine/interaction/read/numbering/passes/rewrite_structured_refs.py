# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rewrite structured refs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.normalize import renumber_snapshot
from computer_use.interaction.read.numbering.model import PURE, NumberingRun, _References
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.pipeline.core import Control


def rewrite_structured_refs(value: NumberingRun, control: Control) -> tuple[SemanticSnapshot, ...]:
    """Rebuild each changed semantic snapshot with its final native-to-display alias map."""
    snapshots = []
    for snapshot, mapping in zip(value.snapshots, value.aliases):
        if snapshot.refs == {ref: native for native, ref in mapping.items()}:
            snapshots.append(snapshot)
            continue
        snapshots.append(renumber_snapshot(snapshot, _References(snapshot.facts["session"], mapping)))
    return tuple(snapshots)


stage = Pass.define(
    name="rewrite_structured_refs",
    function=rewrite_structured_refs,
    contract=Contract(NumberingRun, tuple, PURE),
)
