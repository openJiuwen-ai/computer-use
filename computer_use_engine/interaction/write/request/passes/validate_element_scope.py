# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate retained element provenance inside the observed write boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.query.instances.scope import refresh
from computer_use.interaction.requests import ExecuteAction
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.pipeline.core import Control


def create(*, controller: SessionController):
    async def validate(request: ExecuteAction, control: Control) -> ExecuteAction:
        """Keep the original snapshot while refreshing only its scope evidence.

        This runs before native input, under the same failure-observation and
        deadline boundary. A closed Window is never rebound to a new one.
        """
        if request.snapshot is not None:
            owner, snapshot = request.owner, request.snapshot
            controller.interfaces.validate_snapshot(owner, snapshot, allow_unknown=True)
            # UIHandle exposes the separate instance catalogue to internal pipelines.
            # pylint: disable-next=protected-access
            directory = await refresh(controller, owner) if owner._instance.session is not None else None
            controller.interfaces.validate_snapshot(owner, snapshot)
            if directory is not None and request.on_pre_input_directory is not None:
                request.on_pre_input_directory(directory)
        return request

    return validate


stage = Pass.factory(
    name="validate_element_scope",
    factory=create,
    contract=Contract(ExecuteAction, ExecuteAction, {Effect.QUERY, Effect.RESOURCE}),
)
