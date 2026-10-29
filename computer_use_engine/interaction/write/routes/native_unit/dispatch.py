# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared execution of a selected native command within its existing call scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import AttemptOutcome

if TYPE_CHECKING:
    from computer_use.interaction.write.model import NativeInvocation
    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort


async def send_native_unit(invocation: NativeInvocation, native: NativePort, state: WriteRunState) -> AttemptOutcome:
    """Execute one native command and pair its receipt with the resolved write."""
    plan, command = invocation.unit.plan, invocation.unit.command
    bound = plan.resolved.bound
    step = await native.execute(bound.operation, len(state.steps) + 1, command, invocation.budget)
    return AttemptOutcome(plan.resolved, command, step)
