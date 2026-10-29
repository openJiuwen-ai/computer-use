# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from computer_use.errors import ComputerUseError

if TYPE_CHECKING:
    from typing import Iterable

    from computer_use.interaction.records import Remaining
    from computer_use.interaction.write.model import ResolutionFrame
    from computer_use.native.contracts import Facts, Intent, OperationTarget


class ResolutionParts(NamedTuple):
    """Original write intent, target, operation token, and shared call budget."""

    intent: Intent
    target: OperationTarget
    operation: str
    remaining: Remaining


def parts(frame: ResolutionFrame) -> ResolutionParts:
    """Extract the fixed intent, identity, native operation token, and remaining budget."""
    bound = frame.request.bound
    return ResolutionParts(bound.spec.intent, bound.spec.target, bound.operation, bound.remaining)


def require_actuators(capabilities: Iterable[str], facts: Facts) -> None:
    """Require driver support for binding a resolved internal editor."""
    if "nativeActuators" not in capabilities:
        raise ComputerUseError(
            dict(
                code="mode_unsupported",
                effect="none",
                target=facts.target_label,
                detail="The driver cannot bind the selected internal editor.",
                next="read the current target before continuing; do not replay uncertain input",
            )
        )
