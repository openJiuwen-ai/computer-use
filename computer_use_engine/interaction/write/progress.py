# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One owner for attempts/effects and a pure write replanning Gate."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import AttemptOutcome, AttemptView, RecordedStep, ResolutionMemory
from computer_use.interaction.write.routes.policy import Attempts, preparation_pair_available, route_key
from computer_use.native.contracts import Cleanup, ClickSelectionDecision, Confirmation, Dispatch, StepResult
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
    *,
    observation_reason: str | None = None,
) -> ComputerUseError:
    """Build an error whose reported effect includes accumulated preparation and dispatch.

    Args:
        code: Public error category.
        target: Human-readable target label.
        detail: Explanation of the failing operation.
        dispatched: Furthest known dispatch progress for this write.
        preparation: Preparation effects recorded before the failure.
        minimum_effect: Lower bound supplied by the original error: none, possible, or partial.

    Returns:
        An error with effect severity derived from write progress.
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
    error = ComputerUseError({"code": code, "effect": effect, "target": target, "detail": detail})
    # Internal failure metadata requests observation without changing the public error.
    # pylint: disable-next=protected-access
    error._observation_reason = observation_reason
    return error


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
    native_mutation_possible: bool = False
    requested_input_possible: bool = False


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
        steps: Attempted native commands and their receipts in execution order.
        preparation: Preparation effects accumulated from native receipts.
        dispatched: Furthest known input dispatch progress.
        memory: Resolution evidence retained between attempts.
        last_route_failure: Latest preparation or route-unavailable receipt.
        in_flight: Whether a native unit is currently executing and may have unrecorded effects.
    """

    attempts: Attempts = field(default_factory=Attempts)
    steps: list[RecordedStep] = field(default_factory=list)
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
            frozenset(a.preparation_pairs),
        )
        return ProgressView(
            history,
            self.dispatched,
            tuple(self.preparation),
            self.last_route_failure,
            self.native_mutation_possible,
            self.requested_input_possible,
        )

    @property
    def native_mutation_possible(self) -> bool:
        """Derive effects from the same recorded receipts, without another history."""
        return any(step.receipt is not None and _receipt_has_native_mutation(step.receipt) for step in self.steps)

    @property
    def requested_input_possible(self) -> bool:
        """Explicit requested-input evidence is distinct from context preparation."""
        return any(step.receipt is not None and _receipt_has_requested_input(step.receipt) for step in self.steps)

    def reserve_preparation(self, command: UnitCommand, facts: Facts) -> None:
        """Consume one recipient allowance before its selected native preparation starts.

        Background/foreground and unit-specific attempts on the same pair keep
        their existing route history. A new observation alone consumes nothing,
        and a ready dispatch does not require another preparation allowance.
        """
        if not command.prepare and not (
            command.primitive.click is not None and command.primitive.click.admission == "preparationRequired"
        ):
            return
        if self.dispatched != Dispatch.NOT_DISPATCHED:
            raise failure(
                "execution_unknown",
                facts.target_label,
                "Requested input may already have been dispatched; no further preparation was started.",
                self.dispatched,
                self.preparation,
            )
        if not preparation_pair_available(command, self.attempts):
            raise failure(
                "input_unavailable",
                facts.target_label,
                "The selected receiver still needs preparation after the receiver preparation allowance was consumed.",
                self.dispatched,
                self.preparation,
            )
        self.attempts.preparation_pairs.add((command.actuator, command.receiver))

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
        elif decision.action == "route_unavailable":
            self.last_route_failure = cast("StepResult", outcome.step)
            self.attempts.unavailable.add(route_key(command))
        elif (
            command is not None
            and command.prepare
            and cast("StepResult", outcome.step).dispatch == Dispatch.NOT_DISPATCHED
        ):
            route = route_key(command)
            self.attempts.prepared.add((route, command.foreground))
            if decision.action == "preparation_unavailable":
                self.last_route_failure = cast("StepResult", outcome.step)
                # A refused preparation mode is already consumed above. It
                # does not make this dispatch route unavailable if a different
                # native unit later establishes the same receiver.
                if not command.foreground:
                    self.attempts.background_unavailable.add(route)
        if decision.error is not None:
            raise decision.error


def _receipt_has_native_mutation(step: StepResult) -> bool:
    """Retain explicit effects even if a receipt omits a preparation label."""
    impact = step.impact
    return impact is not None and (
        any(effect.state != "notInvoked" for effect in impact.effects)
        or impact.foreground_changed.value == "yes"
        or impact.clipboard_restoration in ("restored", "ownershipLost", "failed")
        or impact.omitted_effects > 0
    )


def _effect_free_route_refusal(step: StepResult, progress: ProgressView) -> bool:
    """Late permission discovery has the stricter no-effects continuation contract."""
    return (
        step.retry_reason == "routeUnavailable"
        and step.dispatch == progress.dispatched == Dispatch.NOT_DISPATCHED
        and step.cleanup == Cleanup.COMPLETE
        and not step.preparation
        and not progress.preparation
        and not progress.native_mutation_possible
        and not _receipt_has_native_mutation(step)
    )


def _receipt_has_requested_input(step: StepResult) -> bool:
    return step.impact is not None and any(
        effect.stage == "input" and effect.state != "notInvoked" for effect in step.impact.effects
    )


def _route_refusal_permits_recovery(step: StepResult, progress: ProgressView) -> bool:
    """Original-target preparation may continue; requested input cannot replay."""
    return (
        step.retry_reason == "routeUnavailable"
        and step.dispatch == progress.dispatched == Dispatch.NOT_DISPATCHED
        and step.cleanup == Cleanup.COMPLETE
        and not progress.requested_input_possible
        and not _receipt_has_requested_input(step)
    )


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
            step.dispatch == Dispatch.NOT_DISPATCHED
            and step.cleanup.value == "complete"
            and not progress.requested_input_possible
            and not _receipt_has_requested_input(step)
            and click.permits_replanning
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
                    observation_reason="preconditionChanged" if step.retry_reason == "preconditionChanged" else None,
                ),
            )
    if command.primitive.click is None and step.error_code == "permission_required":
        # A late transport qualification can discover HID in a previously
        # unknown candidate. Only a completely effect-free native refusal can
        # close that candidate and preserve an independent permitted route.
        if _effect_free_route_refusal(step, progress):
            return WriteDecision("next_route", action="route_unavailable")
        return WriteDecision(
            "stop",
            error=failure(
                step.error_code,
                facts.target_label,
                step.detail,
                progress.dispatched,
                progress.preparation,
            ),
        )
    if command.prepare and (progress.requested_input_possible or _receipt_has_requested_input(step)):
        return WriteDecision(
            "stop",
            error=failure(
                "execution_unknown",
                facts.target_label,
                "Native preparation reported possible requested input; no further input was sent.",
                progress.dispatched,
                progress.preparation,
                minimum_effect="possible",
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
            if step.error_code == "input_unavailable" and _route_refusal_permits_recovery(step, progress):
                return WriteDecision("next_route", action="route_unavailable")
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
        if _route_refusal_permits_recovery(step, progress):
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
    from computer_use.native.contracts import Facts, UnitCommand
