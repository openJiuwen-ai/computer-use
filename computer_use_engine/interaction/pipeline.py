"""One explicit read/write/query branch per internal system request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.agent.pipeline import AgentDispatch
from computer_use.interaction.passes.is_agent_interface import stage as is_agent_interface
from computer_use.interaction.passes.parse_request import stage as parse_request
from computer_use.interaction.passes.select_request_kind import stage as select_request_kind
from computer_use.interaction.query.pipeline import QueryPipeline
from computer_use.interaction.read.pipeline import ReadPipeline
from computer_use.interaction.write.request.pipeline import ControllerWritePipeline
from computer_use.pipeline import Pipeline, Select, Sequence

if TYPE_CHECKING:
    from typing import Any

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Step


class SessionPipeline(Pipeline["InteractionRequest", "InteractionResult"]):
    """Route session requests through the read, write, or query flow.

    The request type determines the execution branch. Public-handle requests
    enter that branch through the UI adapter, while internal requests enter
    its core pipeline.

    Attributes:
        controller: Session services shared by the branch pipelines and UI adapter.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[InteractionRequest, InteractionResult]:
        controller = self.controller

        def branch(kind: str, core: Step[Any, Any]) -> Step[InteractionRequest, InteractionResult]:
            """Wrap a core branch with public-handle request dispatch."""
            return Select(
                name=kind + "Request",
                selector=is_agent_interface(),
                branches={True: AgentDispatch(controller), False: core},
            )

        return Sequence(
            name="SessionPipeline",
            steps=[
                parse_request(),
                Select(
                    name="InteractionKind",
                    selector=select_request_kind(),
                    branches={
                        "read": branch("Read", ReadPipeline(controller)),
                        "write": branch("Write", ControllerWritePipeline(controller)),
                        "query": branch("Query", QueryPipeline(controller)),
                    },
                ),
            ],
        )


if TYPE_CHECKING:
    from computer_use.interaction.records import InteractionResult
