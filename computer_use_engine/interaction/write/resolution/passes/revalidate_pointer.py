# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Revalidate pointer."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.resolution.control_part import ControlPartQuery
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def revalidate_pointer(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Revalidate the control-part pointer hit retained by an earlier attempt.

        Attach a confirmed actuator and its dependencies to the inspection scope;
        otherwise retain the original scope for subsequent target inspection.
        """
        intent, target, operation, remaining = parts(frame)
        query = ControlPartQuery(native, operation, target, remaining, intent=intent)
        selected = await query.resolve(frame.request.memory.pointer_member)
        return (
            replace(frame, scope={**frame.scope, "actuator": selected, "basis": query.basis})
            if selected is not None
            else frame
        )

    return revalidate_pointer


stage = Pass.factory(
    name="ControlPartPipeline",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
