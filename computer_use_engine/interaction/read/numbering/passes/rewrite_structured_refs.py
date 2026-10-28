# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rewrite structured refs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.pipeline.core import Control
from dataclasses import replace

from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.model import PURE, NumberingRun, _References
from computer_use.pipeline import Contract, Pass


def rewrite_structured_refs(value: NumberingRun, control: Control) -> tuple[SemanticSnapshot, ...]:
    """Rebuild each changed semantic snapshot with its final native-to-display alias map."""
    return tuple(
        snapshot
        if snapshot.refs == {ref: native for native, ref in mapping.items()}
        else replace(
            normalize_facts(
                snapshot.native,
                _References(snapshot.facts["session"], mapping),
                driver_features=snapshot.driver_features,
                scope_kind=snapshot.scope_kind,
                operation_capabilities=(
                    snapshot.collection.operation_capabilities if snapshot.collection is not None else None
                ),
                diagnostic_fields=(snapshot.collection.diagnostic_fields if snapshot.collection is not None else None),
                field_sources=(snapshot.collection.field_sources if snapshot.collection is not None else None),
            ),
            collection=snapshot.collection,
        )
        for snapshot, mapping in zip(value.snapshots, value.aliases)
    )


stage = Pass.define(
    name="rewrite_structured_refs",
    function=rewrite_structured_refs,
    contract=Contract(NumberingRun, tuple, PURE),
)
