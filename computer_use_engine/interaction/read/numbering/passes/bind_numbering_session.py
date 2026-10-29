# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind numbering session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.model import PURE, NumberingRequest, NumberingRun
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def bind_numbering_session(request: NumberingRequest, control: Control) -> NumberingRun:
    """Bind the snapshot sequence to a display allocator owned by their common driver session."""
    snapshots, references = request.snapshots, request.references
    session = snapshots[0].facts["session"]
    references = references if references is not None else DisplayReferences(session)
    if references.session != session or any(snapshot.facts["session"] != session for snapshot in snapshots):
        raise ValueError("Display references belong to a different native session")
    return NumberingRun(snapshots, references)


stage = Pass.define(
    name="bind_numbering_session",
    function=bind_numbering_session,
    contract=Contract(NumberingRequest, NumberingRun, PURE),
)
