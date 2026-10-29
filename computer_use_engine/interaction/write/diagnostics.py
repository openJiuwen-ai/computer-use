# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Best-effort projections of retained click decisions; never query native state."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from computer_use.diagnostics import diagnostic

if TYPE_CHECKING:
    from computer_use.interaction.write.model import AttemptOutcome
    from computer_use.interaction.write.progress import ProgressView, WriteDecision
    from computer_use.interaction.write.routes.selection.model import SelectionRequest
    from computer_use.native.contracts import ClickSelectionExclusion, UnitCommand
    from computer_use.pipeline.core import Control


def record_click_selection(
    control: Control,
    request: SelectionRequest,
    exclusions: tuple[ClickSelectionExclusion, ...],
    selected: UnitCommand | None,
) -> None:
    """Expose selection evidence without labels, values, intent payloads or extra reads."""
    try:
        diagnostic(
            dict(
                kind="click_selection",
                callId=control.call_id,
                path=list(control.path),
                attempt=control.attempt,
                observed=request.facts.observed,
                target=asdict(request.facts.binding),
                mode=request.policy.mode,
                foregroundAllowed=request.policy.foreground_allowed,
                selected=None
                if selected is None
                else dict(
                    routeFamily=selected.primitive.click.route_family,
                    delivery=selected.primitive.click.delivery,
                    foreground=selected.foreground,
                ),
                candidates=[
                    dict(
                        primitiveId=p.id,
                        routeFamily=p.click.route_family,
                        admission=p.click.admission,
                        preparationOptions=list(p.click.preparation_options),
                    )
                    for p in request.facts.primitives
                    if p.click is not None
                ],
                exclusions=[
                    dict(routeFamily=e.route_family, reason=e.reason, observed=e.observed, step=e.step)
                    for e in exclusions
                ],
            )
        )
    except BaseException:
        # A cancelled/broken optional sink cannot prevent or repeat input.
        pass


def record_click_decision(
    control: Control, outcome: AttemptOutcome, decision: WriteDecision, progress: ProgressView, step_number: int
) -> None:
    """Publish the Gate result and its retained evidence, without re-running policy."""
    try:
        command, step = outcome.command, outcome.step
        if command is None or command.primitive.click is None or step is None:
            return
        click = step.click
        diagnostic(
            dict(
                kind="click_decision",
                callId=control.call_id,
                path=list(control.path),
                attempt=control.attempt,
                operation=outcome.resolved.bound.operation,
                step=step_number,
                target=asdict(command.target),
                routeFamily=command.primitive.click.route_family,
                delivery=command.primitive.click.delivery,
                foreground=command.foreground,
                decision=decision.kind,
                action=decision.action,
                errorType=type(decision.error).__name__ if decision.error is not None else None,
                dispatch=step.dispatch.value,
                cleanup=step.cleanup.value,
                confirmation=step.confirmation.value,
                retryReason=step.retry_reason,
                errorCode=step.error_code,
                refreshesUsed=progress.history.precondition_changes,
                continuation=None if click is None else click.continuation.kind,
                preparationAllowsReplan=False if click is None else click.permits_replanning,
                preparation=[]
                if click is None
                else [
                    dict(
                        kind=p.kind,
                        dispatch=p.dispatch.value,
                        effect=p.effect,
                        continuation=p.continuation,
                        wait=p.wait,
                    )
                    for p in click.preparation_evidence
                ],
            )
        )
    except BaseException:
        pass
