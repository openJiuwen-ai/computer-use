# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Freeze typed atomic display fields from the final source-preserved projection."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import AtomDocument, AtomRequest
from ..planning import build_atoms


def execute(request: AtomRequest, control: Control) -> AtomDocument:
    """Select source-bound field slots and exact native-detail destinations."""
    return build_atoms(request.projection, request.branch)


stage = Pass.define(
    name="plan_interface_atomic_fields",
    function=execute,
    contract=Contract(
        AtomRequest,
        AtomDocument,
        {Effect.PURE},
        requires=("source-preserved projection", "captured native-detail routes"),
        writes=("typed atomic display fields", "field and action destinations"),
    ),
)
