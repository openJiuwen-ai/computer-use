# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Route public handle requests and the two internal request kinds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.agent.pipeline import AgentDispatch, AgentInterfacePipeline
from computer_use.interaction.passes.select_route import AGENT_ROUTES, QUERY, WRITE, stage as select_route
from computer_use.interaction.query.pipeline import QueryPipeline
from computer_use.interaction.write.request.pipeline import ControllerWritePipeline
from computer_use.pipeline import Pipeline, Select

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.requests import InteractionRequest
    from computer_use.pipeline.core import Step


class SessionPipeline(Pipeline["InteractionRequest", "InteractionResult"]):
    """Route session requests to the UI adapter or an internal execution flow.

    One selector names the branch from the request type. Public-handle
    requests share the UI adapter branch, keyed by their effect class so the
    trace still records read/write/query. Internal branches serve standalone
    desktop/session requests. The adapter composes the same write and lookup
    instances directly, without re-entering this router.

    Attributes:
        write: Shared action execution flow.
        lookup: Shared element lookup flow.
        interfaces: Public-handle adapter composing those flows.
    """

    def __init__(self, controller: SessionController) -> None:
        self.write = ControllerWritePipeline(controller)
        self.lookup = QueryPipeline(controller)
        self.interfaces = AgentInterfacePipeline(controller, self.write, self.lookup)
        super().__init__(root=self.build())

    def build(self) -> Step[InteractionRequest, InteractionResult]:
        agent = AgentDispatch(self.interfaces)
        return Select(
            name="SessionPipeline",
            selector=select_route(),
            branches={
                **{route: agent for route in AGENT_ROUTES},
                WRITE: self.write,
                QUERY: self.lookup,
            },
        )


if TYPE_CHECKING:
    from computer_use.interaction.records import InteractionResult
