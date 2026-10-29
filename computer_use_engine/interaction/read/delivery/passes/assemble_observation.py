# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble observation."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.delivery.model import PURE, ObservationDraft
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.model import AppObservation
    from computer_use.pipeline.core import Control


def assemble(draft: ObservationDraft, control: Control) -> AppObservation:
    """Finalize the application observation with its directories, components, aliases, and
    scope evidence.
    """
    from computer_use.interaction.read.model import AppObservation

    acquired = draft.source.acquired
    plan, components = acquired.plan, acquired.components
    cast("Callable[[], int]", control.remaining)()
    return AppObservation(
        selection=plan.selection,
        target=acquired.target,
        applications=plan.applications,
        source_directory=components.source_directory,
        directory=acquired.directory,
        tree=components.tree,
        image=components.image,
        document=draft.document,
        refs=MappingProxyType(draft.refs),
        windows=MappingProxyType(draft.windows),
        gaps=acquired.gaps,
        scope_confirmed=acquired.scope_confirmed,
        launch=plan.launch,
        menus=components.menus,
        overview=draft.overview,
    )


stage = Pass.define(
    name="assemble_observation",
    function=assemble,
    contract=Contract(ObservationDraft, object, PURE),
)
if TYPE_CHECKING:
    from typing import Callable
