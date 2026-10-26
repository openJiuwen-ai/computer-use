"""Keep current-receiver delegation inside the originally requested container."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import ResolutionFrame
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Any

    from computer_use.pipeline.core import Control


def validate_current_receiver(frame: ResolutionFrame, _: object) -> ResolutionFrame:
    """Validate the receiver selected for container input forwarding.

    Compare current focus and its ancestors with the resolved target or actuator.
    Return the frame when that receiver still owns input, and report an unavailable
    route when focus moved elsewhere.
    """
    spec, facts = frame.request.bound.spec, frame.facts
    if spec.forward_scope is None:
        return frame
    focused = facts.input.focused_node
    accepted = {spec.target.node}
    if facts.actuator is not None:
        accepted.add(facts.actuator.node)
    if focused not in accepted and not accepted.intersection(facts.input.focus_ancestors):
        raise ComputerUseError(
            dict(
                code="input_unavailable",
                effect="none",
                target=facts.target_label,
                detail="The current receiver changed after the container input target was resolved.",
                next="read the container or specify the intended control; no requested input was sent",
            )
        )
    return frame


def execute(value: Any, control: Control) -> ResolutionFrame:
    return validate_current_receiver(value, control)


stage = Pass.define(
    name="ValidateCurrentReceiver",
    function=validate_current_receiver,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
