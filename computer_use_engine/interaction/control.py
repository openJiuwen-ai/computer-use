# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Carry native call identity and remaining time into a child flow."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.pipeline import Control
from computer_use.pipeline.core import Node, T, U

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.records import Remaining
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Step


def scope_control(port: NativePort, remaining: Remaining, parent: Control) -> Control:
    """Combine an active native call's identity with the parent's diagnostics.

    Args:
        port: Native port whose call service supplies the active call metadata.
        remaining: Milliseconds remaining on the enclosing native operation.
        parent: Control inherited from the containing pipeline.

    Returns:
        Child control using the native call ID when the port provides one.
    """
    factory = getattr(getattr(port, "calls", None), "control", None)
    if not callable(factory):
        return parent.with_remaining(remaining)
    current = factory(remaining)
    if not isinstance(current, Control):
        return parent.with_remaining(remaining)
    return replace(
        parent, remaining=remaining, call_id=current.call_id, diagnostic=parent.diagnostic or current.diagnostic
    )


class NativeScopeBody(Node[T, U]):
    """Propagate a native operation's call budget and metadata to a child flow.

    Each execution extracts the lease's remaining-time callback from its input
    and combines it with available native call metadata for the child control.
    The child receives the original value and returns its processing result.

    Attributes:
        port: Native port supplying the active call's identity and diagnostics.
        body: Child flow executed within that call.
        remaining: Function extracting the lease's budget callback from the input.
    """

    def __init__(self, port: NativePort, child: Step[T, U], remaining: Callable[[T], Remaining]) -> None:
        self.port, self.body, self.remaining = port, child, remaining
        self.children = (child,)
        super().__init__("NativeScopeBody", child.contract)

    def _run(self, value: T, control: Control) -> U:
        return self.body.run(value, scope_control(self.port, self.remaining(value), control))

    async def _arun(self, value: T, control: Control) -> U:
        return await self.body.arun(value, scope_control(self.port, self.remaining(value), control))


if TYPE_CHECKING:
    from computer_use.pipeline.core import T, U
