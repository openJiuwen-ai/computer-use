# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind menu context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.directory.service import select_surface
from computer_use.interaction.read.delivery.menu.model import (
    PURE,
    MenuObservationPlan,
    MenuObservationRequest,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[MenuObservationRequest, Control], MenuObservationPlan]:
    def context(request: MenuObservationRequest, control: Control) -> MenuObservationPlan:
        """Resolve the request's root or selected surface as the native menu-collection context."""
        selected = request.selection.root if request.selection else None
        if request.selection is not None and "nativeApplicationRoots" not in services.port.connection.capabilities:
            selected = select_surface(request.directory, request.selection.root.root_window, follow_modal=False)
        return MenuObservationPlan(request, selected)

    return context


stage = Pass.factory(
    name="bind_menu_context",
    factory=create,
    contract=Contract(MenuObservationRequest, MenuObservationPlan, PURE),
)
