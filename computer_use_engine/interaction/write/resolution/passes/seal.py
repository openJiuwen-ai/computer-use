# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Seal."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import ResolutionFrame, ResolvedTarget, ResolvedWrite
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    pass


def seal(frame: ResolutionFrame, _: object) -> ResolvedWrite:
    """Seal the resolved target, collected facts, and selection dependencies for route choice."""
    request = frame.request
    resolved = ResolvedTarget(
        request.bound.spec,
        cast("Facts", frame.facts),
        tuple(frame.scope.get("basis", ())),
        frame.needs_editing_evidence,
    )
    return ResolvedWrite(request.bound, resolved, request.memory)


stage = Pass.define(
    name="seal_resolved_target",
    function=seal,
    contract=Contract(ResolutionFrame, ResolvedWrite),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import Facts
