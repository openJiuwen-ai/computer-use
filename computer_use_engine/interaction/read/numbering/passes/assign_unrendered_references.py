# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assign unrendered references."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.helpers import assign_native
from computer_use.interaction.read.numbering.model import RESOURCE, NumberingRun
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assign_unrendered_references(value: NumberingRun, control: Control) -> NumberingRun:
    """Allocate aliases for native objects retained outside each snapshot's rendered preview."""
    for snapshot, mapping in zip(value.snapshots, value.aliases):
        for native in snapshot.refs.values():
            if native not in mapping:
                assign_native(snapshot, mapping, value.references, native)
    return value


stage = Pass.define(
    name="assign_unrendered_references",
    function=assign_unrendered_references,
    contract=Contract(NumberingRun, NumberingRun, RESOURCE, writes=("session reference allocator",)),
)
