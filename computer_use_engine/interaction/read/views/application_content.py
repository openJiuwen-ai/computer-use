# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect an exact App-level content root when the window directory has no AX root."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.read.acquisition.components.support import collect_component
from computer_use.interaction.read.limits import DEFAULT_WINDOW_MAX_NODES
from computer_use.native.contracts import Binding
from computer_use.session.interfaces import SurfaceOwner
from computer_use.session.state import AppState

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.components.support import ObservationGap
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import UIHandle
    from computer_use.pipeline.core import Control


async def collect_application_content(
    controller: SessionController, application: str, control: Control
) -> tuple[tuple[Binding, SemanticSnapshot] | None, ObservationGap | None]:
    """Reuse read admission, directory and fact collection under the original call budget."""
    async def collect() -> tuple[Binding, SemanticSnapshot] | None:
        with controller.port.calls.claim(30000) as remaining:
            async with controller.port.calls.operation(None, remaining) as read:
                roots = await controller.reader.directory.collect(
                    read, {"kind": "roots", "application": application, "applicationContent": True}, _control=control
                )
            candidates = [item for item in roots["roots"] if item["kind"] == "applicationContent"]
            if not candidates and not roots["stopReasons"]:
                return None
            if len(candidates) != 1:
                detail = "; ".join(roots["stopReasons"]) or "the application content root is not unique"
                raise local_failure(detail, "observation_incomplete")
            root = candidates[0]
            target = Binding(roots["session"], root["instance"], None, root["surface"], root["node"])
            async with controller.port.calls.operation(replace(target, node=None), remaining) as read:
                tree = await controller.reader.reader.collect(
                    read, node=target.node, max_nodes=DEFAULT_WINDOW_MAX_NODES,
                    scope_kind="applicationContent", _control=control,
                )
        return target, tree

    return await collect_component(controller.port, "application content", collect)


def remember_application_content(
    controller: SessionController, ui: UIHandle, target: Binding, tree: SemanticSnapshot
) -> SurfaceOwner:
    """Keep content refs in the App while preserving their own native scope."""
    state = AppState(
        controller.state(ui).selector,
        running=controller.state(ui).running,
        # pylint: disable-next=protected-access
        refs=ui._instance.refs,
        query_target=target,
        query_scope_kind="applicationContent",
    )
    controller.remember_observation(state, target, tree)
    return SurfaceOwner(controller, state)
