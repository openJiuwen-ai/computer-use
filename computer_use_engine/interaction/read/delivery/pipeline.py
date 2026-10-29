# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Number only selected attempts, then assemble the fixed-window observation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.model import PURE, ObservationDraft
from computer_use.interaction.read.delivery.passes.append_window_overview import (
    stage as append_window_overview,
)
from computer_use.interaction.read.delivery.passes.assemble_observation import (
    stage as assemble_observation,
)
from computer_use.interaction.read.delivery.passes.assemble_observation_header import (
    stage as assemble_observation_header,
)
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.model import AppObservation
    from computer_use.interaction.read.numbering.observation.model import RenderedObservation
    from computer_use.pipeline.core import Control, Step


class _PrepareAppMenus(Node["ObservationDraft", "ObservationDraft"]):
    """Append collected menu sources to the application draft.

    Prepare application-menu snapshots and combine their documents and bindings
    with the window observation. Return the draft for overview and result assembly.

    Attributes:
        presentation: Menu-source presentation flow reused for the collected application
            menus.
    """

    def __init__(self) -> None:
        from computer_use.interaction.read.delivery.menu.pipeline import MenuSourcesPresentationPipeline

        self.presentation = MenuSourcesPresentationPipeline()
        self.children = (self.presentation,)
        super().__init__("PrepareAppMenus", Contract(ObservationDraft, ObservationDraft, PURE))

    def _run(self, draft: ObservationDraft, control: Control) -> ObservationDraft:
        from computer_use.interaction.read.delivery.menu.model import MenuSourceRenderRequest

        menus = draft.source.acquired.components.menus
        if menus is None:
            return draft
        document = self.presentation.run(MenuSourceRenderRequest(menus, compact=True), control)
        refs = dict(draft.refs)
        for part in menus.parts:
            refs.update({ref: replace(part.target, node=node) for ref, node in part.tree.refs.items()})
        return replace(
            draft,
            refs=refs,
            document=DisplayDocument.join(
                (draft.document, "Application menu (separate from the working window):", document)
            ),
        )

    async def _arun(self, value: ObservationDraft, control: Control) -> ObservationDraft:
        return self._run(value, control)


class AppDeliveryPipeline(Pipeline["RenderedObservation", "AppObservation"]):
    """Assemble the application observation from its numbered acquisition.

    Format the window and collection evidence, append menu sources and requested window
    previews, then finalize the application result. Retain the source directories, bindings,
    and scope-confirmation state for publication.

    Attributes:
        services: Application reader supplying window aliases and observation formatting.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[RenderedObservation, AppObservation]:
        services = self.services

        return Sequence(
            name="AppDeliveryPipeline",
            steps=[
                assemble_observation_header(services=services),
                _PrepareAppMenus(),
                append_window_overview(),
                assemble_observation(),
            ],
        )
