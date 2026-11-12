# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit application or window preparation before observation."""

import asyncio
import builtins
from dataclasses import replace

from computer_use.errors import ComputerUseError, format_failure
from computer_use.interaction.query.instances.scope import resolve
from computer_use.native.contracts import ApplicationBinding, Binding
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.protocol.primitives import frozen
from computer_use.session.operations import current_operation_budget

APP_REOPEN_HINT = "To show this running application, call app.read(view='both', allow_foreground=True)."


def can_reopen_application(controller, directory):
    """Require a completed empty window catalogue for the app-level preparation route."""
    return (
        "nativeApplicationReopen" in controller.transport.capabilities
        and not directory["surfaces"]
        and not directory["stopReasons"]
    )


async def refresh_after_preparation(controller, ui, view, preparation):
    """Refresh the original scope and give asynchronous application reopen time to create a window."""
    directory, _ = await resolve(controller, ui, view)
    if "reopenStatus" not in preparation:
        return directory
    budget = current_operation_budget()
    for delay in (0.08, 0.16, 0.32, 0.64):
        if directory["surfaces"] or directory["stopReasons"]:
            break
        remaining = budget.remaining() if budget is not None else None
        if remaining is not None and remaining <= int(delay * 1000) + 100:
            break
        await asyncio.sleep(delay)
        directory, _ = await resolve(controller, ui, view)
    return directory


class ObservationPreparationPipeline(Node):
    """Prepare one observed window or running application in a bounded lease."""

    def __init__(self, port):
        self.port = port
        self.children = ()
        super().__init__(
            "ObservationPreparation",
            Contract(Binding | ApplicationBinding, dict, {Effect.QUERY, Effect.PREPARE, Effect.RESOURCE}),
        )

    async def _arun(self, target, control):
        window = replace(target, node=None, capture=None) if isinstance(target, Binding) else None
        receipt = None
        try:
            async with self.port.calls.open(window, 3000) as lease:
                receipt = await self.port.prepare_observation(
                    lease.token, lease.target if window is not None else target, lease.remaining()
                )
            return receipt
        except BaseException as error:
            if receipt is not None:
                annotate_preparation_failure(error, receipt)
            raise

    def _run(self, target, control):
        raise TypeError("Observation preparation requires arun()")


def annotate_preparation_failure(error, preparation):
    """Preserve requested preparation across a subsequent read failure."""
    if isinstance(error, (builtins.KeyboardInterrupt, builtins.SystemExit)):
        return error
    # Preserve cancellation semantics while retaining evidence for its owner.
    error.observation_preparation = frozen(preparation)
    reopen = "application reopen=requested; " if "reopenStatus" in preparation else ""
    note = (
        f"Observation preparation: {reopen}activation={preparation['activation']}; "
        f"raise={preparation['raiseStatus']}."
    )
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
