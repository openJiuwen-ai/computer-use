# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One owner for attempts/effects and a pure write replanning Gate."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import AttemptOutcome, AttemptView, ResolutionMemory
from computer_use.interaction.write.routes.policy import Attempts, route_key
from computer_use.native.contracts import ClickSelectionDecision, Confirmation, Dispatch, StepResult
from computer_use.protocol.primitives import required_item

if TYPE_CHECKING:
    from typing import Iterable

    from computer_use.protocol.records import Record


def failure(
    code: str,
    target: str,
    detail: str,
    dispatched: Dispatch,
    preparation: Iterable[str],
    minimum_effect: str = "none",
    next_step: str | None = None,
) -> ComputerUseError:
    """Build an error whose reported effect includes accumulated preparation and dispatch.

    Args:
        code: Public error category.
        target: Human-readable target label.
        detail: Explanation of the failing operation.
        dispatched: Furthest known dispatch progress for this write.
        preparation: Preparation effects recorded before the failure.
        minimum_effect: Lower bound supplied by the original error: none, possible, or partial.
        next_step: Suggested recovery when the write is known to have no effect.

    Returns:
        An error with effect severity and recovery guidance derived from write progress.
    """
    effect = (
        "partial"
        if dispatched == Dispatch.PARTIAL
        else "possible"
        if dispatched != Dispatch.NOT_DISPATCHED or preparation
        else "none"
    )
    severity = {"none": 0, "possible": 1, "partial": 2}
    if required_item(severity, minimum_effect) > required_item(severity, effect):
        effect = minimum_effect
    recovery = (
        next_step
        if effect == "none" and next_step
        else "read the current target before continuing; do not replay uncertain input"
    )
    return ComputerUseError(dict(code=code, effect=effect, target=target, detail=detail, next=recovery))


@dataclass(frozen=True)
class ProgressView:
    """Immutable effects and attempt history used to classify the next write transition.

    Attributes:
        history: Unavailable and prepared routes for the current write.
        dispatched: Furthest known dispatch progress, including uncertainty.
        preparation: Ordered preparation effects reported by native units.
        last_route_failure: Most recent unavailable-route receipt used in terminal error reporting.
    """

    history: AttemptView
    dispatched: Dispatch
    preparation: tuple[str, ...]
    last_route_failure: StepResult | None


@dataclass(frozen=True)
class WriteDecision:
    """Gate decision applied once by the write-state owner.

    Attributes:
        kind: Completion, reinspection, route change, or terminal failure.
        action: History update associated with precondition or route unavailability.
        receipt: Completed write receipt, when the decision finishes the operation.
        error: Terminal exception to propagate after recording the decision.
    """

    kind: Literal["complete", "reinspect", "next_route", "stop"]
    action: str | None = None
    receipt: Record | None = None
    error: BaseException | None = None


@dataclass
class WriteRunState:
    """Own attempt history, retained evidence, and native effects for one write call.

    Attributes:
        attempts: Mutable route history shared across resolution and selection attempts.
        steps: Native unit receipts in execution order.
        preparation: Preparation effects accumulated from native receipts.
        dispatched: Furthest known input dispatch progress.
        memory: Resolution evidence retained between attempts.
        last_route_failure: Latest preparation or route-unavailable receipt.
        in_flight: Whether a native unit is currently executing and may have unrecorded effects.
    """

    attempts: Attempts = field(default_factory=Attempts)
    steps: list[StepResult] = field(default_factory=list)
    preparation: list[str] = field(default_factory=list)
    dispatched: Dispatch = Dispatch.NOT_DISPATCHED
    memory: ResolutionMemory = field(default_factory=ResolutionMemory)
    last_route_failure: StepResult | None = None
    in_flight: bool = False
    selections: list[ClickSelectionDecision] = field(default_factory=list)

    def view(self) -> ProgressView:
        """Snapshot effects and route history for selection and Gate decisions."""
        a = self.attempts
        history = AttemptView(
            frozenset(a.unavailable),
            frozenset(a.background_unavailable),
            frozenset(a.prepared),
            a.precondition_changes,
            a.click_refresh,
            a.failed_steps,
        )
        return ProgressView(history, self.dispatched, tuple(self.preparation), self.last_route_failure)

    def consume(self, outcome: AttemptOutcome, decision: WriteDecision) -> None:
        """Apply a Gate decision to route history and propagate its terminal error."""
        command = cast("UnitCommand", outcome.command)
        if command is not None and command.primitive.click is not None:
            step = cast("StepResult", outcome.step)
            route = route_key(command)
            if decision.kind in ("reinspect", "next_route"):
                self.last_route_failure = step
                self.attempts.failed_steps[route] = len(self.steps)
            if decision.action == "precondition_changed":
                self.attempts.precondition_changes += 1
                # The old attempt stays consumed. Only the explicit refresh
                # below can reuse it; a later preparation failure cannot make
                # that activation mode look like an untried route again.
                self.attempts.unavailable.add(route)
                self.attempts.click_refresh = route if step.click.continuation.kind == "sameCandidateRefresh" else None
            elif decision.action in ("route_unavailable", "preparation_unavailable"):
                self.attempts.click_refresh = None
                self.attempts.unavailable.add(route)
                if decision.action == "preparation_unavailable" and not command.foreground:
                    self.attempts.background_unavailable.add(route)
                elif decision.action == "route_unavailable":
                    self.attempts.unavailable.add(route_key(replace(command, foreground=not command.foreground)))
            if decision.error is not None:
                raise decision.error
            return
        if decision.action == "precondition_changed":
            route = route_key(command)
            self.attempts.precondition_changes += 1
            self.attempts.prepared = {p for p in self.attempts.prepared if p[0] != route}
            self.attempts.background_unavailable.discard(route)
        elif (
            command is not None
            and command.prepare
            and cast("StepResult", outcome.step).dispatch == Dispatch.NOT_DISPATCHED
        ):
            route = route_key(command)
            self.attempts.prepared.add((route, command.foreground))
            if decision.action == "preparation_unavailable":
                self.last_route_failure = cast("StepResult", outcome.step)
                (self.attempts.unavailable if command.foreground else self.attempts.background_unavailable).add(route)
        elif decision.action == "route_unavailable":
            self.last_route_failure = cast("StepResult", outcome.step)
            self.attempts.unavailable.add(route_key(command))
        if decision.error is not None:
            raise decision.error


def classify_write_progress(outcome: AttemptOutcome, progress: ProgressView) -> WriteDecision:
    """Classify a native receipt as completion, reinspection, another route, or failure.

    Preparation success can require fresh resolution; precondition changes and
    route refusals use the remaining attempt allowance. Partial or uncertain
    input ends the write with its recorded effect.
    """
    if outcome.completed is not None:
        return WriteDecision("complete", receipt=dict(outcome.completed))
    command, step = cast("UnitCommand", outcome.command), cast("StepResult", outcome.step)
    facts = outcome.resolved.target.facts
    intent = outcome.resolved.bound.spec.intent
    if command.primitive.click is not None:
        click = step.click
        if (
            click is None
            or click.continuation.target != command.target
            or click.continuation.route_family != command.primitive.click.route_family
        ):
            return WriteDecision("stop", error=ValueError("Click receipt does not retain the selected contract"))
        # The native continuation is a constraint, never an independent retry
        # authorization. Only this Gate consumes its finite history/budget.
        can_replan = (
            step.dispatch == Dispatch.NOT_DISPATCHED and step.cleanup.value == "complete" and click.permits_replanning
        )
        if can_replan and step.retry_reason == "preconditionChanged" and progress.history.precondition_changes < 1:
            return WriteDecision("reinspect", action="precondition_changed")
        if can_replan and click.continuation.kind == "reselectWithinScope":
            if step.retry_reason == "preparationUnavailable":
                return WriteDecision("next_route", action="preparation_unavailable")
            if step.retry_reason == "routeUnavailable":
                return WriteDecision("next_route", action="route_unavailable")
        if step.dispatch != Dispatch.DISPATCHED or step.error_code is not None:
            return WriteDecision(
                "stop",
                error=failure(
                    step.error_code or "input_unavailable",
                    facts.target_label,
                    step.detail,
                    progress.dispatched,
                    progress.preparation,
                ),
            )
    if (
        command.primitive.click is None
        and step.dispatch == Dispatch.NOT_DISPATCHED
        and step.retry_reason == "preconditionChanged"
    ):
        if progress.history.precondition_changes < 1:
            return WriteDecision("reinspect", action="precondition_changed")
    if command.prepare:
        if step.dispatch != Dispatch.NOT_DISPATCHED:
            return WriteDecision("stop", error=ValueError("Preparation unexpectedly dispatched the requested input"))
        if step.error_code is not None:
            if step.retry_reason == "preparationUnavailable":
                return WriteDecision("next_route", action="preparation_unavailable")
            return WriteDecision(
                "stop",
                error=failure(
                    step.error_code, facts.target_label, step.detail, progress.dispatched, progress.preparation
                ),
            )
        if intent.kind == "focus" and step.confirmation == Confirmation.CONFIRMED:
            receipt: Record = dict(target=facts.target_label, status="verified", detail=step.detail)
            if step.context is not None:
                receipt["context"] = dict(step.context)
            return WriteDecision("complete", receipt=receipt)
        return WriteDecision("reinspect")
    if step.dispatch in (Dispatch.UNKNOWN, Dispatch.PARTIAL) or step.error_code is not None:
        if step.dispatch == Dispatch.NOT_DISPATCHED and step.retry_reason == "routeUnavailable":
            return WriteDecision("next_route", action="route_unavailable")
        return WriteDecision(
            "stop",
            error=failure(
                step.error_code or "execution_unknown",
                facts.target_label,
                step.detail,
                progress.dispatched,
                progress.preparation,
            ),
        )
    if step.dispatch != Dispatch.DISPATCHED:
        if not (
            intent.kind in ("replace", "launch")
            and step.dispatch == Dispatch.NOT_DISPATCHED
            and step.confirmation == Confirmation.CONFIRMED
        ):
            return WriteDecision("stop", error=ValueError("A completed input unit did not report dispatch"))
    receipt: Record = dict(
        target=facts.target_label,
        status="verified" if step.confirmation == Confirmation.CONFIRMED else "dispatched",
        detail=step.detail,
    )
    if step.context is not None:
        receipt["context"] = dict(step.context)
    if intent.kind == "launch":
        if step.application is None:
            return WriteDecision(
                "stop", error=ValueError("Application launch completed without a running-instance receipt")
            )
        receipt["application"], receipt["dispatch"] = dict(step.application), step.dispatch.value
    return WriteDecision("complete", receipt=receipt)


if TYPE_CHECKING:
    from computer_use.native.contracts import UnitCommand
