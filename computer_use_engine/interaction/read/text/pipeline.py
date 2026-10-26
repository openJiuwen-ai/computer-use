"""Captured text continuation, without reacquiring or retargeting an element."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import localFailure
from computer_use.interaction.read.acquisition.text.model import CapturedTextRequest
from computer_use.interaction.read.acquisition.text.pipeline import CapturedTextPipeline
from computer_use.interaction.read.text.model import TextReadPlan
from computer_use.interaction.read.text.passes.materialize_captured_text import (
    stage as materialize_captured_text,
)
from computer_use.interaction.read.text.passes.prepare_captured_text_read import (
    stage as prepare_captured_text_read,
)
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import ReadText
    from computer_use.pipeline.core import Control, Step
    from computer_use.types import TextPage


class CaptureTextPage(Node["TextReadPlan", "TextReadPlan"]):
    """Read one page from the element's retained text source.

    Run captured-text collection using the request's source and range. Attach the native
    page to the text-read plan for public result conversion.

    Attributes:
        capture: Captured-text flow selecting local-value or native-reference reads.
    """

    def __init__(self, controller: SessionController) -> None:
        self.capture = CapturedTextPipeline(controller.port)
        self.children = (self.capture,)
        super().__init__("CaptureTextPage", Contract(TextReadPlan, TextReadPlan, self.capture.contract.effects))

    async def _arun(self, plan: TextReadPlan, control: Control) -> TextReadPlan:
        from dataclasses import replace

        try:
            page = await self.capture.arun(
                CapturedTextRequest(
                    cast("CapturedField", plan.source),
                    offset=plan.request.options.get("offset", 0),
                    length=plan.request.options.get("length", 4096),
                ),
                control,
            )
        except ValueError as error:
            if cast("CapturedField", plan.source).value is None:
                raise
            raise localFailure(str(error), "continue with the next offset returned by readText") from error
        return replace(plan, page=page)

    def _run(self, value: TextReadPlan, control: Control) -> TextReadPlan:
        raise TypeError("Text continuations require arun()")


class ControllerTextReadPipeline(Pipeline["ReadText", "TextPage"]):
    """Read a public text page from retained element text.

    Validate the selected snapshot and resolve its captured text source, then collect the
    requested page. Convert the native page to the public result with its source metadata.

    Attributes:
        controller: Session services for snapshot validation, native text collection, and
            page conversion.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[ReadText, TextPage]:
        controller = self.controller
        return Sequence(
            name="ControllerTextReadPipeline",
            steps=[
                prepare_captured_text_read(validate_snapshot=controller.validateSnapshot),
                CaptureTextPage(controller),
                materialize_captured_text(captured_text=controller._captured_text),
            ],
        )


if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.reader import CapturedField
