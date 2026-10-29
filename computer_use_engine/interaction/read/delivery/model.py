# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.observation.model import RenderedObservation
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.images.overview import WindowOverview
    from computer_use.native.contracts import Binding


PURE, RESOURCE = {Effect.PURE}, {Effect.RESOURCE}


@dataclass(frozen=True)
class ObservationDraft:
    """Application observation structure and bindings assembled before final delivery.

    Attributes:
        source: Numbered acquisition and the selected tree's rendered representation.
        refs: Display element aliases mapped to native bindings.
        windows: Display window aliases mapped to native surface identities.
        document: Structured window and menu presentation awaiting final rendering.
        overview: Related-window preview and its source metadata, when collected.
    """

    source: RenderedObservation
    refs: dict[str, Binding]
    windows: dict[str, str]
    document: DisplayDocument
    overview: WindowOverview | None
