# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize evidenced fields and records within the selected visual regions."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.build_records import (
    apply as build_records,
)
from computer_use.interaction.read.presentation.interface.structure.algorithms.build_record_blocks import (
    apply as build_record_blocks,
)
from computer_use.interaction.read.presentation.interface.geometric.algorithms.structure import organize_subfields
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Organize evidenced fields and records within the selected visual regions."""
    draft = build_records(draft)
    draft = build_record_blocks(draft)
    draft = organize_subfields(draft)
    return draft


stage = Pass.define(
    name="GeometricStructurePass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable captured facts and explicit source membership",),
        writes=("local geometric structure plan",),
    ),
)
