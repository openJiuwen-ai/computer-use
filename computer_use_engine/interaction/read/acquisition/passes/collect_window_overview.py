# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect window overview."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.model import CollectedAttempt
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[CollectedAttempt, Control], Awaitable[CollectedAttempt]]:
    async def overview(value: CollectedAttempt, control: Control) -> CollectedAttempt:
        """Attach requested related-window previews using the attempt's source directory and image."""
        state, components = value.state, value.components
        if not state.plan.limits.get("overview", True):
            return value
        overview = await services.overviews.collect(
            components.source_directory,
            services.window_aliases(components.source_directory),
            cast("Callable[[], int]", control.remaining),
            current=components.image,
            root=state.plan.selection.root.root_window,
        )
        return replace(value, components=replace(components, overview=overview))

    return overview


stage = Pass.factory(
    name="collect_window_overview",
    factory=create,
    contract=Contract(CollectedAttempt, CollectedAttempt, {Effect.QUERY, Effect.RESOURCE}),
)
