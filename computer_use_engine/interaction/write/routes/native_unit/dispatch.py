# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared execution of a selected native command within its existing call scope."""

from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import replace

from computer_use.interaction.write.model import AttemptOutcome, RecordedStep

if TYPE_CHECKING:
    from computer_use.interaction.write.model import NativeInvocation
    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort


async def send_native_unit(invocation: NativeInvocation, native: NativePort, state: WriteRunState) -> AttemptOutcome:
    """Execute one native command and pair its receipt with the resolved write."""
    plan, command = invocation.unit.plan, invocation.unit.command
    bound = plan.resolved.bound
    state.steps.append(RecordedStep(command))
    try:
        step = await native.execute(bound.operation, len(state.steps), command, invocation.budget)
    except BaseException as error:
        state.steps[-1] = replace(state.steps[-1], failure_effect=getattr(error, "effect", "possible"))
        raise
    return AttemptOutcome(plan.resolved, command, step)
