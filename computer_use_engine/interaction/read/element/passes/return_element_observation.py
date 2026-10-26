"""Return element observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.element.model import ElementReadPlan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.records import ElementSnapshot
    from computer_use.pipeline.core import Control
    from computer_use.types import Observation


def create(
    *, element_snapshot: Callable[[object, str], ElementSnapshot]
) -> Callable[[ElementReadPlan, Control], tuple[Observation, ElementSnapshot]]:

    def result(plan: ElementReadPlan, control: Control) -> tuple[Observation, ElementSnapshot]:
        """Return the published subtree and the refreshed snapshot for its selected native element."""
        target = plan.selected["binding"]
        ref = next((ref for ref, node in cast("SemanticSnapshot", plan.tree).refs.items() if node == target.node))
        return (cast("Observation", plan.result), element_snapshot(plan.request.owner, ref))

    return result


stage = Pass.factory(
    name="return_element_observation",
    factory=create,
    contract=Contract(ElementReadPlan, tuple),
)
if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
