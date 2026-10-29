# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Remember starting scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app import RunningApplication, StartingApplication
from computer_use.interaction.read.app_read.model import RESOURCE, ApplicationSelection
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def remember(value: ApplicationSelection, control: Control) -> ApplicationSelection:
    """Notify the scope owner of the running identity and any retained installation launch
    receipt.
    """
    if value.request.on_scope is not None:
        scope = RunningApplication(
            value.application["id"], value.application["displayName"], value.applications["session"]
        )
        if value.installation is not None:
            scope = StartingApplication(scope.application, scope.name, scope.session, value.installation, value.launch)
        value.request.on_scope(scope)
    return value


stage = Pass.define(
    name="remember_starting_scope",
    function=remember,
    contract=Contract(ApplicationSelection, ApplicationSelection, RESOURCE),
)
