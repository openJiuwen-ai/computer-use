# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe window choices and recheck the catalogue after overview capture."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.choices.model import (
    WindowChoiceDraft,
    WindowChoiceLease,
)
from computer_use.interaction.read.acquisition.choices.passes.collect_choice_overview import (
    stage as collect_choice_overview,
)
from computer_use.interaction.read.acquisition.choices.passes.format_window_choices import (
    stage as format_window_choices,
)
from computer_use.interaction.read.acquisition.choices.passes.validate_choice_session import (
    stage as validate_choice_session,
)
from computer_use.interaction.read.acquisition.scope.pipeline import DirectoryServiceNode
from computer_use.pipeline import Contract, Pipeline, Scope, Sequence
from computer_use.pipeline.core import Node, T, U

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Awaitable, Callable

    from computer_use.interaction.read.acquisition.choices.model import WindowChoiceRequest
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.model import WindowSelectionRequired
    from computer_use.pipeline.core import Control, Step


class WindowChoicePipeline(Pipeline["WindowChoiceRequest", "WindowSelectionRequired"]):
    """Prepare a window-selection response for an ambiguous read.

    Capture requested candidate previews, then refresh the application surface directory
    under the original deadline. Format the current choices and annotate catalogue changes
    since the previews.

    Attributes:
        services: Application reader providing window aliases, previews, and directory
            queries.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[WindowChoiceRequest, WindowSelectionRequired]:
        services = self.services

        @asynccontextmanager
        async def lease(draft: WindowChoiceDraft, control: Control) -> AsyncIterator[WindowChoiceLease]:
            async with services.port.calls.operation(None, cast("Callable[[], int]", control.remaining)) as read:
                yield WindowChoiceLease(draft, read)

        async def collect(value: WindowChoiceLease, control: Control) -> WindowChoiceDraft:
            current = await services.directory.collect(
                value.read,
                dict(kind="surfaces", application=value.draft.request.application),
                _control=control,
            )
            return replace(value.draft, current=current)

        return Sequence(
            name="WindowChoicePipeline",
            steps=[
                collect_choice_overview(services=services),
                Scope(
                    name="ChoiceDirectoryScope",
                    using=lease,
                    body=NativeScopeBody(
                        services.port,
                        Sequence(
                            name="ChoiceDirectoryRecheck",
                            steps=[
                                validate_choice_session(),
                                DirectoryServiceNode(
                                    "CollectChoiceDirectory",
                                    services.directory,
                                    collect,
                                    WindowChoiceLease,
                                    WindowChoiceDraft,
                                ),
                            ],
                        ),
                        lambda value: value.read.remaining,
                    ),
                ),
                format_window_choices(services=services),
            ],
        )


class WindowChoiceServiceNode(Node[T, U]):
    """Connect a window-choice callback to its declared discovery graph.

    Invoke the callback with the enclosing stage's input and control. Expose window-choice
    collection as the child flow used by the callback.

    Attributes:
        function: Bound callback that adapts its stage data to window-choice collection.
    """

    def __init__(
        self,
        name: str,
        services: NativeAppReader,
        function: Callable[[T, Control], Awaitable[U]],
        input_type: Any = object,
        output_type: Any = object,
    ) -> None:
        self.function = function
        self.children = (WindowChoicePipeline(services),)
        super().__init__(name, Contract(input_type, output_type, self.children[0].contract.effects))

    async def _arun(self, value: T, control: Control) -> U:
        return await self.function(value, control)

    def _run(self, value: T, control: Control) -> U:
        raise TypeError("Window choices require arun()")
