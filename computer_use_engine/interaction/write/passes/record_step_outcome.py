"""Record step outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import AttemptOutcome
from computer_use.native.contracts import Cleanup, Dispatch
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort, state: WriteRunState) -> Callable[[AttemptOutcome, Control], AttemptOutcome]:
    def record_step(outcome: AttemptOutcome, control: Control) -> AttemptOutcome:
        """Record the native unit's receipt and accumulated effects.

        Append the step and preparation effects, update dispatch progress, and publish
        the native receipt event. Retire the session when the receipt reports another
        target or incomplete input cleanup.
        """
        step = outcome.step
        if step is None:
            return outcome
        state.steps.append(step)
        state.preparation.extend(step.preparation)
        if step.click is not None:
            # Evidence cannot disappear merely because a backend omitted prose.
            state.preparation.extend(
                p.kind
                for p in step.click.preparation_evidence
                if (p.dispatch != Dispatch.NOT_DISPATCHED or p.effect != "contextOnly")
                and p.kind not in step.preparation
            )
        if not step.target.same_target(outcome.resolved.bound.spec.target):
            native.calls.retired = True
            state.dispatched = Dispatch.UNKNOWN
            raise ValueError("Native step returned a different target")
        if step.dispatch != Dispatch.NOT_DISPATCHED:
            state.dispatched = step.dispatch
        if outcome.command.primitive.click is not None:
            if (
                step.click is None
                or step.click.continuation.target != step.target
                or step.click.continuation.route_family != outcome.command.primitive.click.route_family
                or any(p.scope != step.target for p in step.click.preparation_evidence)
            ):
                native.calls.retired = True
                state.dispatched = Dispatch.UNKNOWN
                raise ValueError("Native click evidence changed its selected target or delivery")
        control.with_branch(cast("UnitCommand", outcome.command).primitive.unit).emit(
            "native_receipt", frozenset({Effect.RESOURCE}), cleanup=step.cleanup.value, dispatch=step.dispatch.value
        )
        if step.cleanup != Cleanup.COMPLETE:
            native.calls.retired = True
            raise ValueError("Native input cleanup was not confirmed")
        return outcome

    return record_step


stage = Pass.factory(
    name="record_step_outcome",
    factory=create,
    contract=Contract(
        AttemptOutcome,
        AttemptOutcome,
        {Effect.RESOURCE},
        writes=("dispatch", "preparation", "cleanup"),
    ),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import UnitCommand
