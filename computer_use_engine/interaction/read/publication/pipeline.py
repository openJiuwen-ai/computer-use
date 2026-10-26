"""Publish exact captured facts; this flow never queries the desktop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.interaction.read.publication.model import PublicationSources
from computer_use.interaction.read.publication.passes.assemble_publication_text import (
    stage as assemble_publication_text,
)
from computer_use.interaction.read.publication.passes.collect_publication_sources import (
    stage as collect_publication_sources,
)
from computer_use.interaction.read.publication.passes.materialize_observation import (
    stage as materialize_observation,
)
from computer_use.interaction.read.publication.passes.publish_observation_event import (
    stage as publish_observation_event,
)
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.publication.model import PublicationRequest
    from computer_use.pipeline.core import Control, Step
    from computer_use.types import Observation


class PreparePublicationSources(Node["PublicationSources", "PublicationSources"]):
    """Prepare retained source snapshots for observation publication.

    Freeze each compressed projection and its original facts in source order.
    Assembly consumes these structures without formatting their fields or lines.

    Attributes:
        preparation: Pure source-freezing and projection flow for retained snapshots.
    """

    def __init__(self) -> None:
        self.preparation = PrepareSnapshotPipeline()
        self.children = (self.preparation,)
        super().__init__("PreparePublicationSources", Contract(PublicationSources, PublicationSources))

    def _run(self, value: PublicationSources, control: Control) -> PublicationSources:
        from dataclasses import replace

        parts = []
        for _, snapshot in value.observations:
            parts.append(self.preparation.run(SnapshotRenderRequest(snapshot, compact=True), control))
        return replace(value, rendered=tuple(parts))

    async def _arun(self, value: PublicationSources, control: Control) -> PublicationSources:
        return self._run(value, control)


class ObservationPublicationPipeline(Pipeline["PublicationRequest", "Observation"]):
    """Publish an observation together with its retained source evidence.

    Prepare the requested snapshots, assemble structured public and detailed views, and create
    the lazy observation event. Publish that event after its bindings and window metadata have
    been attached.

    Attributes:
        controller: Session services for continuation hints, observation metadata, and event
            publication.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[PublicationRequest, Observation]:
        controller = self.controller
        return Sequence(
            name="ObservationPublicationPipeline",
            steps=[
                collect_publication_sources(),
                PreparePublicationSources(),
                assemble_publication_text(
                    capabilities_of=lambda: controller.transport.capabilities,
                    page_hints=lambda *args, **options: controller.pages.plan_hints(*args, **options),
                ),
                materialize_observation(
                    overview_image=controller._overview_image,
                    window_ref=controller._window_ref,
                ),
                publish_observation_event(publish_observation=controller.publishObservation),
            ],
        )
