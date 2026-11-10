# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit window preparation before observation; never an implicit query effect."""

import builtins
from dataclasses import replace

from computer_use.errors import ComputerUseError, format_failure
from computer_use.native.contracts import Binding
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.protocol.primitives import frozen


class ObservationPreparationPipeline(Node):
    """Borrow only the original window and a bounded native preparation lease."""

    def __init__(self, port):
        self.port = port
        self.children = ()
        super().__init__(
            "ObservationPreparation",
            Contract(Binding, dict, {Effect.QUERY, Effect.PREPARE, Effect.RESOURCE}),
        )

    async def _arun(self, target, control):
        target = replace(target, node=None, capture=None)
        receipt = None
        try:
            async with self.port.calls.open(target, 3000) as lease:
                receipt = await self.port.prepare_observation(lease.token, lease.target, lease.remaining())
            return receipt
        except BaseException as error:
            if receipt is not None:
                annotate_preparation_failure(error, receipt)
            raise

    def _run(self, target, control):
        raise TypeError("Observation preparation requires arun()")


def annotate_preparation_failure(error, preparation):
    """A later read failure cannot erase an already requested activation/raise."""
    if isinstance(error, (builtins.KeyboardInterrupt, builtins.SystemExit)):
        return error
    # Preserve cancellation semantics while retaining evidence for its owner.
    error.observation_preparation = frozen(preparation)
    note = f"Observation preparation: activation={preparation['activation']}; raise={preparation['raiseStatus']}."
    if not isinstance(error, ComputerUseError):
        error.add_note(note)
        return error
    changed = preparation["activation"] != "notRequested" or preparation["raiseStatus"] in ("requested", "unknown")
    # Preserve subtype-specific lifetime evidence (e.g. FixedWindowInvalidated.binding).
    if changed and error.effect == "none":
        error.effect = "possible"
    error.detail += " " + note
    error.args = (
        format_failure(
            {
                "code": error.code,
                "effect": error.effect,
                "target": error.target,
                "detail": error.detail,
            }
        ),
    )
    return error
