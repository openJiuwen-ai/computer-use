# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import AcquiredObservation
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot

PURE, RESOURCE = {Effect.PURE}, {Effect.RESOURCE}


@dataclass(frozen=True)
class RenderedObservation:
    """Completed acquisition paired with its prepared structured display.

    Attributes:
        acquired: Window acquisition result before numbering and presentation.
        rendered: Frozen semantic projection with display order and captured source facts.
    """

    acquired: AcquiredObservation
    rendered: PreparedSnapshot | None
