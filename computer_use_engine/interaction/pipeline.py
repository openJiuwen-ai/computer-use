"""One explicit read/write/query branch per internal system request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.agent.pipeline import AgentDispatch
from computer_use.interaction.passes.select_route import AGENT_ROUTES, QUERY, READ, WRITE, stage as select_route
from computer_use.interaction.query.pipeline import QueryPipeline
from computer_use.interaction.read.pipeline import ReadPipeline
from computer_use.interaction.write.request.pipeline import ControllerWritePipeline
from computer_use.pipeline import Pipeline, Select

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Step


class SessionPipeline(Pipeline["InteractionRequest", "InteractionResult"]):
    """Route session requests through the read, write, or query flow.

    One selector names the branch from the request type. Public-handle
    requests share the UI adapter branch, keyed by their effect class so the
    trace still records read/write/query; internal requests enter their core
    pipeline directly.

    Attributes:
        controller: Session services shared by the branch pipelines and UI adapter.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[InteractionRequest, InteractionResult]:
        controller = self.controller
        agent = AgentDispatch(controller)
        return Select(
            name="SessionPipeline",
            selector=select_route(),
            branches={
                **{route: agent for route in AGENT_ROUTES},
                READ: ReadPipeline(controller),
                WRITE: ControllerWritePipeline(controller),
                QUERY: QueryPipeline(controller),
            },
        )


if TYPE_CHECKING:
    from computer_use.interaction.records import InteractionResult
