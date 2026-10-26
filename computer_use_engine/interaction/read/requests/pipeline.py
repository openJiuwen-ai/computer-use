"""Dispatch controller read variants without inventing an empty observation IR."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.application.pipeline import ControllerApplicationReadPipeline
from computer_use.interaction.read.desktop.pipeline import ControllerDesktopReadPipeline
from computer_use.interaction.read.element.pipeline import ControllerElementReadPipeline
from computer_use.interaction.read.requests.passes.select_read_kind import stage as select_read_kind
from computer_use.interaction.read.text.pipeline import ControllerTextReadPipeline
from computer_use.pipeline import Pipeline, Select

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Step


class SessionReadPipeline(Pipeline["InteractionRequest", "ReadResult"]):
    """Select the observation flow for a typed session read request.

    Route application, desktop, element, and text requests to their corresponding controller
    flows. Return the selected flow's public observation or captured-text page.

    Attributes:
        controller: Session owner supplying the branch-specific read services.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[InteractionRequest, ReadResult]:
        controller = self.controller

        return Select(
            name="SessionReadPipeline",
            selector=select_read_kind(),
            branches={
                "application": ControllerApplicationReadPipeline(controller),
                "desktop": ControllerDesktopReadPipeline(controller),
                "element": ControllerElementReadPipeline(controller),
                "text": ControllerTextReadPipeline(controller),
            },
        )


if TYPE_CHECKING:
    from computer_use.interaction.records import ReadResult
