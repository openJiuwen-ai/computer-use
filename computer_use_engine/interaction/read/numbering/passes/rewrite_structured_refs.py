"""Rewrite structured refs."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.normalize import normalize_facts
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
        normalized = normalize_facts(
            snapshot.native,
            _References(snapshot.facts["session"], mapping),
            driver_features=snapshot.driver_features,
            scope_kind=snapshot.scope_kind,
            operation_capabilities=(
                snapshot.collection.operation_capabilities if snapshot.collection is not None else None
            ),
        )
        snapshots.append(replace(normalized, collection=snapshot.collection))
    return tuple(snapshots)


stage = Pass.define(
    name="rewrite_structured_refs",
    function=rewrite_structured_refs,
    contract=Contract(NumberingRun, tuple, PURE),
)
