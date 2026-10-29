# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect window pixels within the current component lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.images.passes.collect_pixels import (
    stage as collect_pixels,
)
from computer_use.pipeline import Effect, Pipeline, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.components.model import ComponentLease
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Step


QUERY = {Effect.QUERY}


class ImageCollectionPipeline(Pipeline["ComponentLease", "ComponentLease"]):
    """Collect the requested window pixels within a component operation.

    Run image capture for the current component lease and attach its image or collection
    gaps. Return the updated lease for component assembly.

    Attributes:
        services: Application reader providing native images and capture capability
            evidence.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ComponentLease, ComponentLease]:
        services = self.services

        return Sequence(name="ImageCollectionPipeline", steps=[collect_pixels(services=services)])
