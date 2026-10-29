# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pin operation target."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import BoundWrite, OperationSpec, ResolutionMemory, TargetBinding
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort, state: WriteRunState) -> Callable[[TargetBinding, Control], BoundWrite]:
    def pin_target(value: TargetBinding, control: Control) -> BoundWrite:
        """Pin the resolved identity, ancestor bounds, host policy, and remaining call budget."""
        request, lease = (value.opened.request, value.opened.lease)
        target, within = (value.target, value.within)
        spec = OperationSpec(request.intent, target, within, native.policy, request.forward_scope)
        state.memory = ResolutionMemory(within=within)
        return BoundWrite(spec, lease.token, cast("Callable[[], int]", control.remaining))

    return pin_target


stage = Pass.factory(
    name="pin_operation_target",
    factory=create,
    contract=Contract(
        TargetBinding,
        BoundWrite,
        {Effect.RESOURCE},
        writes=("fixed operation target", "initial resolution memory"),
    ),
)
