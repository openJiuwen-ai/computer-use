# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Remember running scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app import RunningApplication, SelectedApplicationWindow
from computer_use.interaction.read.app_read.model import RESOURCE, DirectoryLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def remember(value: DirectoryLease, control: Control) -> DirectoryLease:
    """Notify the scope owner of the resolved running application or selected native window."""
    application, request = value.application, value.call.request
    if application["isRunning"] and request.on_scope is not None:
        scope = RunningApplication(application["id"], application["displayName"], value.read.target.session)
        if request.window is not None:
            scope = SelectedApplicationWindow(scope.application, scope.name, scope.session, request.window)
        request.on_scope(scope)
    return value


stage = Pass.define(
    name="remember_running_scope",
    function=remember,
    contract=Contract(DirectoryLease, DirectoryLease, RESOURCE),
)
