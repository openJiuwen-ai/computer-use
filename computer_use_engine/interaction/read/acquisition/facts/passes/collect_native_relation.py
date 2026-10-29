# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect native relation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.model import QUERY, OpenRelationRead
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def create(*, port: NativePort) -> Callable[[OpenRelationRead, Control], Awaitable[Record]]:
    async def collect(value: OpenRelationRead, control: Control) -> Record:
        """Read one native relationship page using the requested cursor and active operation
        deadline.
        """
        request, read = value.request, value.read
        from computer_use.interaction.read.facts.index import observation_index

        reply = await port.relation(
            read.token, read.target, request.property, request.cursor, request.limit, read.remaining()
        )
        facts = observation_index(reply.observation)
        if len(facts["relations"]) != 1:
            raise ValueError("A relationship read requires one collected relation")
        return dict(
            kind="relation",
            session=reply.observation.session,
            surface=reply.metadata.surface,
            platform=reply.observation.backend,
            observed=facts["observed"],
            relation=facts["relations"][0],
        )

    return collect


stage = Pass.factory(
    name="collect_native_relation",
    factory=create,
    contract=Contract(OpenRelationRead, dict, QUERY),
)
