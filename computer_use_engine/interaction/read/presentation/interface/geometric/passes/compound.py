# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compose one visual control from an exact native wrapper/control/caption path."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.compound.composition import (
    compose_compound_controls,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Keep a semantic control primary and every coincident wrapper independently visible."""
    return compose_compound_controls(draft)


stage = Pass.define(
    name="GeometricCompoundPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("complete original single-child paths and comparable control bounds",),
        writes=("compound display blocks with unchanged real identities and action owners",),
    ),
)
