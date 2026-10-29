# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble menu observation."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.menu.model import PURE, MenuTextDraft
from computer_use.interaction.read.model import MenuAppObservation
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assemble_menu_observation(value: MenuTextDraft, control: Control) -> MenuAppObservation:
    """Return the menu snapshots, rechecked directory, window aliases, and joined presentation
    text.
    """
    observed, request = value.observed, value.observed.request
    return MenuAppObservation(
        request.application["id"],
        request.application["displayName"],
        request.selection,
        observed.menu,
        observed.directory,
        MappingProxyType(value.windows),
        DisplayDocument.join(value.lines),
        request.launch,
    )


stage = Pass.define(
    name="assemble_menu_observation",
    function=assemble_menu_observation,
    contract=Contract(MenuTextDraft, MenuAppObservation, PURE),
)
