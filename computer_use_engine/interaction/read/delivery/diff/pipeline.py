"""Plan structured delivery and generate its text at the terminal stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.diff.model import PURE, PlannedDelivery
from computer_use.interaction.read.delivery.diff.passes.plan_full_delivery import (
    stage as plan_full_delivery,
)
from computer_use.interaction.read.delivery.diff.passes.plan_observation_diff import (
    stage as plan_observation_diff,
)
from computer_use.interaction.read.delivery.diff.passes.select_full_or_diff import (
    stage as select_full_or_diff,
)
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.rendering.document.model import DocumentRenderRequest
from computer_use.interaction.read.presentation.rendering.document.pipeline import DocumentRenderingPipeline
from computer_use.pipeline import Contract, Pipeline, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.read.delivery.diff.model import DeliverySelection
    from computer_use.pipeline.core import Control, Step


class _RenderPlannedDelivery(Node["PlannedDelivery", "str"]):
    """Run the shared terminal rendering flow for the selected observation delivery.

    Attributes:
        rendering: Explicit child flow rendering frozen snapshot sources and final text.
    """

    def __init__(self) -> None:
        self.rendering = DocumentRenderingPipeline()
        self.children = (self.rendering,)
        super().__init__("RenderPlannedDelivery", Contract(PlannedDelivery, str, PURE))

    def _run(self, value: PlannedDelivery, control: Control) -> str:
        observation = value.observation
        document = observation.get("document")
        if isinstance(document, DisplayDocument):
            request = DocumentRenderRequest(document, value.plan, observation.get("publicApi", False))
            return self.rendering.run(request, control)
        return observation["state"]["text"]

    async def _arun(self, value: PlannedDelivery, control: Control) -> str:
        return self._run(value, control)


class DeliverySelectionPipeline(Pipeline["DeliverySelection", "str"]):
    """Choose full or diff text for an observation delivery.

    Use the request and compatible previous baseline to select complete text or a contextual
    tree diff. Return the selected text for the observation event.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[DeliverySelection, str]:
        return Sequence(
            name="DeliverySelectionPipeline",
            steps=[
                Select(
                    name="ChooseDeliveryPlan",
                    selector=select_full_or_diff(),
                    branches={True: plan_full_delivery(), False: plan_observation_diff()},
                ),
                _RenderPlannedDelivery(),
            ],
        )
