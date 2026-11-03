# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure projections of the write owner's attempted commands and native evidence."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.contracts import HostPolicy
    from computer_use.protocol.records import Record


def action_impact(state: WriteRunState, policy: HostPolicy) -> Record:
    """Keep declared effects distinct from what each native invocation reported.

    Missing receipts and incomplete instrumentation remain unknown. Nothing in
    this projection qualifies a route, confirms business success, or retries.
    """
    attempts: list[Record] = []
    for recorded in state.steps:
        command, receipt = recorded.command, recorded.receipt
        primitive = command.primitive
        actual = None
        if receipt is not None and receipt.impact is not None:
            actual = asdict(receipt.impact)
            actual["foreground_changed"] = receipt.impact.foreground_changed.value
            actual["effects"] = tuple(actual["effects"])
        potential = primitive.preparation_impact
        attempts.append(
            dict(
                route=primitive.click.route_family if primitive.click is not None else primitive.unit,
                declared_global_input=primitive.global_input.value,
                preparation_potential=None
                if potential is None
                else {key: value.value for key, value in asdict(potential).items()},
                foreground_requested=command.foreground,
                preparation_only=command.prepare,
                preparation=receipt.preparation if receipt is not None else None,
                dispatch=(
                    receipt.dispatch.value
                    if receipt is not None
                    else "notDispatched"
                    if recorded.failure_effect == "none"
                    else "partial"
                    if recorded.failure_effect == "partial"
                    else "unknown"
                ),
                cleanup=receipt.cleanup.value if receipt is not None else "unknown",
                actual=actual,
            )
        )
    return dict(global_input_allowed=policy.allow_global_input, attempts=tuple(attempts))
